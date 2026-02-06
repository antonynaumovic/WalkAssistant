import asyncio
import time
from main import SpeedChart

async def run_test():
    c = SpeedChart()
    print('initial len', len(c.values), 'last', c.values[-1])

    # small jump
    start = time.perf_counter()
    c.push_value(2)
    if c._anim_task is not None:
        await c._anim_task
    print('after small jump len', len(c.values), 'last', c.values[-1], 'elapsed', time.perf_counter()-start)

    # large jump
    start = time.perf_counter()
    c.push_value(500)
    if c._anim_task is not None:
        await c._anim_task
    print('after large jump len', len(c.values), 'last', c.values[-1], 'elapsed', time.perf_counter()-start)

    # another large jump
    start = time.perf_counter()
    c.push_value(1000)
    if c._anim_task is not None:
        await c._anim_task
    print('after very large jump len', len(c.values), 'last', c.values[-1], 'elapsed', time.perf_counter()-start)

    # many pushes
    start = time.perf_counter()
    for v in [10,20,30,500,600,5,2,700,800,3,4,100]:
        c.push_value(v)
        if c._anim_task is not None:
            await c._anim_task
    print('after sequence len', len(c.values), 'last', c.values[-1], 'elapsed', time.perf_counter()-start)

asyncio.run(run_test())
