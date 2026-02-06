import asyncio
import logging
import time
from socket import AF_INET, SOCK_DGRAM, socket
from typing import Any, Optional

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
_bind_smoothing: float = 0.8

# Event callbacks
_status_callbacks = []  # callbacks(status: bool)
_message_callbacks = []  # callbacks(message_tuple)
_ip_callbacks = [] # callbacks(ip_str: str)

logger = logging.getLogger("OSC Server")
logger.setLevel(logging.DEBUG)


def get_ip_address():
	s = socket(AF_INET, SOCK_DGRAM)
	s.connect(("8.8.8.8", 80))
	ip_addr = s.getsockname()[0]
	s.close()
	return ip_addr


def handler(addr, *message):
	output = '{:32}'.format(addr)
	if addr.startswith("/bt"):
		for i in range(len(message)):
			output += message[i].hex()
	else:
		for i in range(len(message)):
			output += ' {:8.3f}'.format(message[i])
	print(output)


def acceleration_handler(addr, *message):
	global current_acceleration, last_acceleration_smoothed, last_trigger_time, last_osc_time

	now = time.time()

	last_osc_time = now

	# the message may be passed as (x,y,z) or as (val1, val2, val3,...). Take first three numeric values.
	if len(message) >= 3:
		try:
			x = float(message[0])
			y = float(message[1])
			z = float(message[2])
		except Exception:
			# fallback: ignore malformed message
			return
	else:
		return

	# compute magnitude and smoothing
	magnitude = (x ** 2 + y ** 2 + z ** 2)

	last_acceleration_smoothed = (last_acceleration_smoothed * _bind_smoothing + magnitude * (1 - _bind_smoothing))

	# notify registered message callbacks with parsed data (non-blocking)
	try:
		_notify_message({"time": now, "endpoint": addr, "x": x, "y": y, "z": z, "magnitude": magnitude,
		                 "smoothed": last_acceleration_smoothed, })
	except Exception:
		logger.error("Error notifying message callbacks")


def set_bind_address(addr: Optional[str], port: int = 9000, endpoint: str = "/accelerometer"):
	"""Set the OSC server bind address and port used when starting the server.
	addr may be None to indicate auto-detect (default behaviour) or an IP string like '127.0.0.1'.
	"""

	global _bind_address, _bind_port, _bind_endpoint
	_bind_address = addr
	_bind_port = int(port)
	_bind_endpoint = endpoint


def get_bind_address():
	return _bind_address, _bind_port, _bind_endpoint


def set_smoothing(smoothing: float = 0.8):
	global _bind_smoothing
	_bind_smoothing = smoothing


def get_smoothing():
	return _bind_smoothing


def register_status_callback(cb):
	"""Register a callback (status: bool) notified when the server starts/stops."""
	if cb not in _status_callbacks:
		_status_callbacks.append(cb)

def register_ip_callback(cb):
	"""Register a callback (ip_str: str) notified when the server IP changes."""
	if cb not in _ip_callbacks:
		_ip_callbacks.append(cb)

def unregister_ip_callback(cb):
	if cb in _ip_callbacks:
		_ip_callbacks.remove(cb)

def unregister_status_callback(cb):
	if cb in _status_callbacks:
		_status_callbacks.remove(cb)


def register_message_callback(cb):
	"""Register a callback(message_tuple) called when a new accelerometer message arrives."""
	if cb not in _message_callbacks:
		_message_callbacks.append(cb)


def unregister_message_callback(cb):
	if cb in _message_callbacks:
		_message_callbacks.remove(cb)


def _notify_status(running: bool):
	for cb in list(_status_callbacks):
		try:
			cb(running)
		except Exception:
			logger.error("Error in status callback")

def _notify_ip(ip: str):
	for cb in list(_ip_callbacks):
		try:
			cb(ip)
		except Exception:
			logger.error("Error in ip callback")


def _notify_message(msg):
	for cb in list(_message_callbacks):
		try:
			cb(msg)
		except Exception:
			logger.error("Error in message callback")


async def start_async_osc_server(addr: Optional[str] = None, port: Optional[int] = None,
                                 smoothing: Optional[float] = None, endpoint: Optional[str] = None):
	"""Start the AsyncIO OSC UDP server on the caller's running event loop.

	This coroutine is designed to be started with asyncio.create_task() from the Flet app's
	event loop, so it runs in the background without blocking the UI.

	It stores the transport/protocol in module globals and properly closes the transport
	when the task is cancelled.
	"""

	global server_transport, server_protocol, _bind_address, _bind_port, _bind_smoothing, _bind_endpoint, ip_str
	if addr is None:
		addr = _bind_address if _bind_address is not None else get_ip_address()
	if port is None:
		port = _bind_port
	if endpoint is None:
		endpoint = _bind_endpoint
	if smoothing is None:
		smoothing = _bind_smoothing
	disp = dispatcher.Dispatcher()
	# Map the accelerometer address; adjust handler signature if needed
	disp.map(endpoint, acceleration_handler)

	# Use the running loop provided by the caller (Flet's event loop).
	# Get the running loop and pass it to the server. Some type-checkers disagree on the exact
	# event loop type, so pass it through an Any-typed variable to avoid spurious warnings.
	loop = asyncio.get_running_loop()
	loop_any: Any = loop
	server = AsyncIOOSCUDPServer((addr, port), disp, loop_any)
	transport, protocol = await server.create_serve_endpoint()
	server_transport = transport
	server_protocol = protocol
	logger.info(f"Async OSC server running on {addr}:{port}, endpoint {endpoint} with smoothing {smoothing}")
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
	"""Legacy helper: run the OSC server on a provided loop (keeps backwards compatibility)."""
	asyncio.set_event_loop(loop)
	loop.run_until_complete(start_async_osc_server())

# Utility used by the UI to check server IP
def get_ip_string() -> str:
	return ip_str is not None

# Utility used by the UI to check server status
def is_running() -> bool:
	"""Return True if the OSC server transport is active (i.e. server appears to be running)."""
	return server_transport is not None
