import asyncio
import osc_server

status_events = []
message_events = []

def status_cb(r):
    print("status_cb ->", r)
    status_events.append(r)

def message_cb(m):
    print("message_cb ->", m)
    message_events.append(m)

async def test():
    osc_server.register_status_callback(status_cb)
    osc_server.register_message_callback(message_cb)
    osc_server.set_bind_address('127.0.0.1', 9010)
    task = asyncio.create_task(osc_server.start_async_osc_server())
    await asyncio.sleep(0.5)
    # simulate a received acceleration message via direct call
    osc_server.acceleration_handler(1.0, 2.0, 3.0)
    await asyncio.sleep(0.1)
    print('status_events:', status_events)
    print('message_events:', message_events)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.1)
    osc_server.unregister_status_callback(status_cb)
    osc_server.unregister_message_callback(message_cb)

asyncio.run(test())
