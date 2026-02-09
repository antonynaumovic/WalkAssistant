import asyncio
import logging
import time
from socket import AF_INET, SOCK_DGRAM, socket
from typing import Any, Callable, Optional
import threading

from config import WalkAssistantConfig
from pythonosc import dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer

# Runtime values
last_acceleration_smoothed = 0
current_acceleration = 0
last_trigger_time = 0
last_osc_time = 0
ip_str = None

# Server transport/protocol so we can close them from another task
server_transport: Optional[object] = None
server_protocol: Optional[object] = None

# Bind configuration (defaults)
_bind_address: Optional[str] = None
_bind_port: int = 9000
_bind_endpoint: str = "/accelerometer"
_bind_groups: list[dict] = []
_bind_smoothing: float = 0.8
_bind_outputs: list[dict] = []
_endpoint_handlers: dict[str, Callable[..., Any]] = {}
_debug_mode: bool = False

# Rate limiter defaults (token bucket)
_rate_limit_hz: float = 60.0
_rate_limit_capacity: float = 60.0
_rate_tokens: float = _rate_limit_capacity
_rate_last_refill: float = time.time()
_rate_dropped: int = 0

# Event callbacks
_status_callbacks = []  # callbacks(status: bool)
_ip_callbacks = []  # callbacks(ip_str: str)
_message_callbacks = []  # callbacks(message_tuple)

logger = logging.getLogger("OSC Server")
# Do not set logger.setLevel() here; logging level is inherited from main.py
handler_logger = logging.getLogger("OSC Handlers")
# Do not set handler_logger.setLevel() here; logging level is inherited from main.py

# Per-output aggregation state
_output_pending_values = {}

# Per-endpoint latest-only message queues and background tasks
_endpoint_queues = {}  # address -> asyncio.Queue
_endpoint_tasks = {}  # address -> asyncio.Task


def _wrap_handler_with_rate_limit(handler: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(addr, *message):
        global _rate_dropped
        try:
            if not _allow_message():
                # Rate limited: drop the message silently but count for debug
                _rate_dropped += 1
                handler_name = getattr(handler, "__name__", str(handler))
                handler_logger.debug(
                    "Dropping message for %s (rate limit). Total dropped=%d",
                    handler_name,
                    _rate_dropped,
                )
                return
        except Exception:
            handler_logger.exception("Error in rate limiter")
            # If the rate limiter fails, still call the handler to avoid message loss
        return handler(addr, *message)

    # give wrapped a useful name for logging/debug
    try:
        wrapped.__name__ = f"rate_limited_{getattr(handler, '__name__', 'handler')}"
    except Exception:
        pass
    return wrapped


def create_handlers(bind_groups: list[dict]):
    handler_logger.debug("create_handlers called with bind_groups: %s", bind_groups)
    global _bind_groups, _bind_outputs, _endpoint_handlers, _output_pending_values, _endpoint_queues, _endpoint_tasks
    handler_logger.debug(f"Bind groups: {bind_groups}")

    _bind_groups = bind_groups or []
    _bind_outputs = []
    _endpoint_handlers = {}  # address -> list of handlers
    _output_pending_values = {}
    _endpoint_queues = {}  # address -> list of queues (one per group/handler)
    _endpoint_tasks = {}  # address -> list of tasks (one per group/handler)

    for index, group in enumerate(_bind_groups):
        endpoints = group.get("endpoints", [])
        group_value_type = group.get("value_type", "")
        group_label = group.get("alias")
        output = {
            "label": group_label,
            "value_type": group_value_type,
            "endpoints": [],
        }
        handler_logger.debug(
            "Creating output %s with type %s", group_label, group_value_type
        )

        # Prepare the aggregation state for this output
        output_key = group_label or f"output_{index}"
        _output_pending_values[output_key] = {}
        endpoint_binds = {}
        # Determine required components for this group based on value_type
        required_components = _required_components_for_type(group_value_type)

        for endpoint in endpoints:
            endpoint_spec = (
                endpoint if isinstance(endpoint, dict) else {"address": endpoint}
            )
            address = endpoint_spec.get("resource")
            if not address:
                handler_logger.warning(
                    "Skipping endpoint without address in group %s", group_label
                )
                continue

            endpoint_value_type = endpoint_spec.get("value_type") or group_value_type
            endpoint_handler = _get_handler_for_value_type(endpoint_value_type)
            endpoint_bind = endpoint_spec.get("bind") or ""
            # Validate endpoint_bind is a subset of required_components
            for c in endpoint_bind:
                if c not in required_components:
                    handler_logger.warning(
                        f"Endpoint {address} bind '{endpoint_bind}' contains invalid component '{c}' for group type '{group_value_type}'"
                    )
            endpoint_binds[address] = endpoint_bind

            # Per-group/endpoint: create a new queue and consumer for this group/handler
            if address not in _endpoint_queues:
                _endpoint_queues[address] = []
                _endpoint_tasks[address] = []

            queue = asyncio.Queue(maxsize=1)
            _endpoint_queues[address].append(queue)

            async def queue_consumer(
                addr=address,
                handler=endpoint_handler,
                bind=endpoint_bind,
                req=required_components,
                output_key=output_key,
                queue=queue,
            ):
                while True:
                    msg = await queue.get()
                    # Map each value in the message to the corresponding bind character (any order)
                    for i, c in enumerate(bind):
                        if c in req and i < len(msg):
                            _output_pending_values[output_key][c] = msg[i]
                    # Check if all required components are present
                    if all(c in _output_pending_values[output_key] for c in req):
                        values = {c: _output_pending_values[output_key][c] for c in req}
                        _notify_message({"output": output_key, **values})
                        _output_pending_values[output_key] = {}

            # Start the consumer task on the running loop
            try:
                loop = asyncio.get_event_loop()
                task = loop.create_task(queue_consumer())
                _endpoint_tasks[address].append(task)
            except RuntimeError:
                # If not in an event loop, start in a thread (for testability)
                def run_in_loop():
                    asyncio.run(queue_consumer())

                import threading

                t = threading.Thread(target=run_in_loop, daemon=True)
                t.start()
                _endpoint_tasks[address].append(t)

            # Register handler that puts the latest message in all queues for this address
            def make_queue_putter(addr=address):
                def putter(_addr, *message):
                    queues = _endpoint_queues[addr]
                    for q in queues:
                        try:
                            if q.full():
                                try:
                                    q.get_nowait()
                                except Exception:
                                    pass
                            q.put_nowait(message)
                        except Exception as e:
                            handler_logger.warning(f"Queue put failed for {addr}: {e}")

                return putter

            wrapped = _wrap_handler_with_rate_limit(make_queue_putter(address))
            if address not in _endpoint_handlers:
                _endpoint_handlers[address] = []
            _endpoint_handlers[address].append(wrapped)
            output["endpoints"].append(
                {
                    "address": address,
                    "value_type": endpoint_value_type,
                    "bind": endpoint_bind,
                }
            )
            handler_logger.debug(
                "Registered latest-only handler %s for %s",
                getattr(wrapped, "__name__", str(wrapped)),
                address,
            )
        _bind_outputs.append(output)


def _required_components_for_type(value_type: str) -> set[str]:
    vt = (value_type or "").lower()
    if vt in ("float", "bool", "boolean", "string", "str", "int", "integer"):
        return {"x"}
    if vt == "vector2":
        return {"x", "y"}
    if vt == "vector3":
        return {"x", "y", "z"}
    if vt == "vector4":
        return {"x", "y", "z", "w"}
    # fallback: allow all
    return {"x", "y", "z", "w"}


def get_ip_address():
    logger.debug("get_ip_address called")
    s = socket(AF_INET, SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip_addr = s.getsockname()[0]
    s.close()
    return ip_addr


def debug_handler(addr, *message):
    handler_logger.debug(
        "debug_handler called with addr: %s, message: %s", addr, message
    )
    now = time.time()
    output = "{:32}".format(addr)
    handler_logger.debug("Debug handler received message on %s: %s", addr, message)
    if addr.startswith("/bt"):
        for i in range(len(message)):
            output += message[i].hex()
    else:
        for i in range(len(message)):
            output += " {:8.3f}".format(message[i])
    print(output)


def float_handler(addr, *message):
    handler_logger.debug(
        "float_handler called with addr: %s, message: %s", addr, message
    )
    now = time.time()
    try:
        _notify_message(
            {
                "time": now,
                "endpoint": addr,
                "value": float(message[0]),
            }
        )
    except Exception:
        logger.error("Error notifying message callbacks")


def int_handler(addr, *message):
    handler_logger.debug("int_handler called with addr: %s, message: %s", addr, message)
    now = time.time()
    try:
        _notify_message(
            {
                "time": now,
                "endpoint": addr,
                "value": int(message[0]),
            }
        )
    except Exception:
        logger.error("Error notifying message callbacks")


def string_handler(addr, *message):
    handler_logger.debug(
        "string_handler called with addr: %s, message: %s", addr, message
    )
    now = time.time()
    try:
        _notify_message(
            {
                "time": now,
                "endpoint": addr,
                "value": str(message[0]),
            }
        )
    except Exception:
        logger.error("Error notifying message callbacks")


def boolean_handler(addr, *message):
    handler_logger.debug(
        "boolean_handler called with addr: %s, message: %s", addr, message
    )
    now = time.time()
    try:
        _notify_message(
            {
                "time": now,
                "endpoint": addr,
                "value": bool(message[0]),
            }
        )
    except Exception:
        logger.error("Error notifying message callbacks")


def vector2_handler(addr, *message):
    handler_logger.debug(
        "vector2_handler called with addr: %s, message: %s", addr, message
    )
    now = time.time()
    try:
        _notify_message(
            {
                "time": now,
                "endpoint": addr,
                "x": float(message[0]),
                "y": float(message[1]),
            }
        )
    except Exception:
        logger.error("Error notifying message callbacks")


def vector3_handler(addr, *message):
    handler_logger.debug(
        "vector3_handler called with addr: %s, message: %s", addr, message
    )
    now = time.time()
    try:
        _notify_message(
            {
                "time": now,
                "endpoint": addr,
                "x": float(message[0]),
                "y": float(message[1]),
                "z": float(message[2]),
            }
        )
    except Exception:
        logger.error("Error notifying message callbacks")


def vector4_handler(addr, *message):
    handler_logger.debug(
        "vector4_handler called with addr: %s, message: %s", addr, message
    )
    now = time.time()
    try:
        _notify_message(
            {
                "time": now,
                "endpoint": addr,
                "x": float(message[0]),
                "y": float(message[1]),
                "z": float(message[2]),
                "w": float(message[3]),
            }
        )
    except Exception:
        logger.error("Error notifying message callbacks")


# def acceleration_handler(addr, *message):
#     global current_acceleration, last_acceleration_smoothed, last_trigger_time, last_osc_time
#
#     now = time.time()
#
#     last_osc_time = now
#
#     # the message may be passed as (x,y,z) or as (val1, val2, val3,...). Take first three numeric values.
#     if len(message) >= 3:
#         try:
#             x = float(message[0])
#             y = float(message[1])
#             z = float(message[2])
#         except Exception:
#             # fallback: ignore malformed message
#             return
#     else:
#         return
#
#     # compute magnitude and smoothing
#     magnitude = x**2 + y**2 + z**2
#
#     last_acceleration_smoothed = (
#         last_acceleration_smoothed * _bind_smoothing + magnitude * (1 - _bind_smoothing)
#     )
#
#     # notify registered message callbacks with parsed data (non-blocking)
#     try:
#         _notify_message(
#             {
#                 "time": now,
#                 "endpoint": addr,
#                 "x": x,
#                 "y": y,
#                 "z": z,
#                 "magnitude": magnitude,
#                 "smoothed": last_acceleration_smoothed,
#             }
#         )
#     except Exception:
#         logger.error("Error notifying message callbacks")


def set_debug_mode(enabled: bool):
    logger.debug("set_debug_mode called with enabled: %s", enabled)
    global _debug_mode
    _debug_mode = enabled


def set_debug_level(level: int):
    logger.debug("set_debug_level called with level: %s", level)
    handler_logger.setLevel(level)
    logger.setLevel(level)


def set_bind_address(
    addr: Optional[str], port: int = 9000, endpoint: str = "/accelerometer"
):
    logger.debug(
        "set_bind_address called with addr: %s, port: %s, endpoint: %s",
        addr,
        port,
        endpoint,
    )
    """Set the OSC server bind address and port used when starting the server.
    addr may be None to indicate auto-detect (default behaviour) or an IP string like '127.0.0.1'.
    """

    global _bind_address, _bind_port, _bind_endpoint
    _bind_address = addr
    _bind_port = int(port)
    _bind_endpoint = endpoint


def get_bind_address():
    logger.debug("get_bind_address called")
    return _bind_address, _bind_port, _bind_endpoint


def set_smoothing(smoothing: float = 0.8):
    logger.debug("set_smoothing called with smoothing: %s", smoothing)
    global _bind_smoothing
    _bind_smoothing = smoothing


def get_smoothing():
    return _bind_smoothing


def set_rate_limit(hz: float = 60.0):
    logger.debug("set_rate_limit called with hz: %s", hz)
    """Set the global maximum incoming messages per second (token bucket)."""
    global _rate_limit_hz, _rate_limit_capacity, _rate_tokens, _rate_last_refill
    _rate_limit_hz = float(hz)
    _rate_limit_capacity = max(1.0, _rate_limit_hz)
    # refill tokens to capacity when changing rate
    _rate_tokens = _rate_limit_capacity
    _rate_last_refill = time.time()


def get_rate_limit() -> float:
    logger.debug("get_rate_limit called")
    return _rate_limit_hz


def _refill_tokens():
    # logger.debug("_refill_tokens called")
    """Refill the token bucket based on elapsed time."""
    global _rate_tokens, _rate_last_refill
    now = time.time()
    elapsed = now - _rate_last_refill
    if elapsed <= 0:
        return
    # add tokens at rate _rate_limit_hz
    added = elapsed * _rate_limit_hz
    _rate_tokens = min(_rate_limit_capacity, _rate_tokens + added)
    _rate_last_refill = now


def _allow_message(tokens: float = 1.0) -> bool:
    # logger.debug("_allow_message called with tokens: %s", tokens)
    """Attempt to consume tokens from the bucket. Returns True if allowed."""
    global _rate_tokens
    try:
        _refill_tokens()
        if _rate_tokens >= tokens:
            _rate_tokens -= tokens
            return True
        return False
    except Exception:
        logger.exception("Rate limiter failed")
        # On failure, allow messages to avoid silent loss
        return True


def register_status_callback(cb):
    logger.debug("register_status_callback called with cb: %s", cb)
    """Register a callback (status: bool) notified when the server starts/stops."""
    if cb not in _status_callbacks:
        _status_callbacks.append(cb)


def register_ip_callback(cb):
    logger.debug("register_ip_callback called with cb: %s", cb)
    """Register a callback (ip_str: str) notified when the server IP changes."""
    if cb not in _ip_callbacks:
        _ip_callbacks.append(cb)


def unregister_ip_callback(cb):
    logger.debug("unregister_ip_callback called with cb: %s", cb)
    if cb in _ip_callbacks:
        _ip_callbacks.remove(cb)


def unregister_status_callback(cb):
    logger.debug("unregister_status_callback called with cb: %s", cb)
    if cb in _status_callbacks:
        _status_callbacks.remove(cb)


def register_message_callback(cb):
    logger.debug("register_message_callback called with cb: %s", cb)
    """Register a callback(message_tuple) called when a new accelerometer message arrives."""
    if cb not in _message_callbacks:
        _message_callbacks.append(cb)


def unregister_message_callback(cb):
    logger.debug("unregister_message_callback called with cb: %s", cb)
    if cb in _message_callbacks:
        _message_callbacks.remove(cb)


def _notify_status(running: bool):
    logger.debug("_notify_status called with running: %s", running)
    for cb in list(_status_callbacks):
        try:
            cb(running)
        except Exception:
            logger.error("Error in status callback")


def _notify_ip(ip: str):
    logger.debug("_notify_ip called with ip: %s", ip)
    for cb in list(_ip_callbacks):
        try:
            cb(ip)
        except Exception:
            logger.error("Error in ip callback")


def _notify_message(msg):
    logger.debug("_notify_message called with msg: %s", msg)
    for cb in list(_message_callbacks):
        try:
            cb(msg)
        except Exception:
            logger.error("Error in message callback")


_VALUE_TYPE_HANDLERS: dict[str, Callable[..., Any]] = {
    "float": float_handler,
    "int": int_handler,
    "integer": int_handler,
    "string": string_handler,
    "str": string_handler,
    "boolean": boolean_handler,
    "bool": boolean_handler,
    "vector2": vector2_handler,
    "vector3": vector3_handler,
    "vector4": vector4_handler,
    "debug": debug_handler,
    # default handler for unknown types
}


def _get_handler_for_value_type(value_type: Optional[str]) -> Callable[..., Any]:
    logger.debug("_get_handler_for_value_type called with value_type: %s", value_type)
    key = (value_type or "").lower()
    logger.debug("Getting handler for key %s", key)
    return _VALUE_TYPE_HANDLERS.get(key, debug_handler)


def get_group_outputs() -> list[dict[str, Any]]:
    logger.debug("get_group_outputs called")
    return _bind_outputs


def get_endpoint_handlers() -> dict[str, Callable[..., Any]]:
    logger.debug("get_endpoint_handlers called")
    return _endpoint_handlers


async def start_async_osc_server(
    addr: Optional[str] = None,
    port: Optional[int] = None,
    smoothing: Optional[float] = None,
    endpoint: Optional[str] = None,
    debug: Optional[bool] = None,
):
    logger.debug(
        "start_async_osc_server called with addr: %s, port: %s, smoothing: %s, endpoint: %s, debug: %s",
        addr,
        port,
        smoothing,
        endpoint,
        debug,
    )
    """Start the AsyncIO OSC UDP server on the caller's running event loop.

    This coroutine is designed to be started with asyncio.create_task() from the Flet app's
    event loop, so it runs in the background without blocking the UI.

    It stores the transport/protocol in module globals and properly closes the transport
    when the task is cancelled.
    """

    global server_transport, server_protocol, _bind_address, _bind_port, _bind_smoothing, _bind_endpoint, ip_str, _debug_mode
    if addr is None:
        addr = _bind_address if _bind_address is not None else get_ip_address()
    if port is None:
        port = _bind_port
    if endpoint is None:
        endpoint = _bind_endpoint
    if smoothing is None:
        smoothing = _bind_smoothing
    if debug is None:
        debug = _debug_mode
    disp = dispatcher.Dispatcher()
    handlers = _endpoint_handlers or {endpoint: [debug_handler]}
    logger.debug(f"Debug mode: {debug}, handlers: {handlers}")
    if debug:
        logger.debug("Debug mode enabled: debug handler will be enabled")
        disp.map("/*", _wrap_handler_with_rate_limit(debug_handler))
    for address, handler_list in handlers.items():

        def multi_handler(addr, *message, handler_list=handler_list):
            for h in handler_list:
                h(addr, *message)

        disp.map(address, multi_handler)

    # Use the running loop provided by the caller (Flet's event loop).
    # Get the running loop and pass it to the server. Some type-checkers disagree on the exact
    # event loop type, so pass it through an Any-typed variable to avoid spurious warnings.
    loop = asyncio.get_running_loop()
    loop_any: Any = loop
    server = AsyncIOOSCUDPServer((addr, port), disp, loop_any)
    transport, protocol = await server.create_serve_endpoint()
    server_transport = transport
    server_protocol = protocol
    logger.info(
        f"Async OSC server running on {addr}:{port}, endpoint {endpoint} with smoothing {smoothing}"
    )
    # notify listeners that the server is running
    _notify_status(True)

    ip_str = f"{addr}:{port}"
    _notify_ip(ip_str)

    try:
        # Run until cancelled
        while True:
            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        # Task cancellation requested; fall through to clean up
        logger.info("OSC server task cancelled, cleaning up...")
        raise
    finally:
        if server_transport is not None:
            try:
                server_transport.close()
                logger.info("OSC server transport closed")
            except Exception:
                logger.error("Error while closing OSC transport")
            finally:
                server_transport = None
                server_protocol = None
                # notify listeners that the server stopped
                _notify_status(False)


def run_async_loop(loop):
    logger.debug("run_async_loop called with loop: %s", loop)
    """Legacy helper: run the OSC server on a provided loop (keeps backwards compatibility)."""
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_async_osc_server())


# Utility used by the UI to check server IP
def get_ip_string() -> str:
    logger.debug("get_ip_string called")
    return ip_str is not None


# Utility used by the UI to check server status
def is_running() -> bool:
    logger.debug("is_running called")
    """Return True if the OSC server transport is active (i.e. server appears to be running)."""
    return server_transport is not None
