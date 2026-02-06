import asyncio
import osc_server

async def test():
    print("Before start: is_running() ->", osc_server.is_running())
    task = asyncio.create_task(osc_server.start_async_osc_server(addr='127.0.0.1', port=9002))
    await asyncio.sleep(0.5)
    print("After start: is_running() ->", osc_server.is_running())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.1)
    print("After cancel: is_running() ->", osc_server.is_running())

asyncio.run(test())
