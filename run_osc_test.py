import asyncio
import osc_server

async def test():
    print("Starting OSC server task on 127.0.0.1:9001")
    task = asyncio.create_task(osc_server.start_async_osc_server(addr='127.0.0.1', port=9001))
    await asyncio.sleep(2)
    print("Cancelling OSC server task")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("OSC server task cancelled (propagated)")

asyncio.run(test())
