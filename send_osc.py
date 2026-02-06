"""
Simple OSC sender to test the WalkAssistant OSC server.
Usage:
    python send_osc.py --host 127.0.0.1 --port 9000 --freq 2 --count 50

Sends `count` messages at `freq` messages per second to the address /accelerometer with 3 floats.
Requires `python-osc`.
"""
import argparse
import asyncio
import random
from pythonosc import udp_client

async def run(host, port, freq, count):
    client = udp_client.SimpleUDPClient(host, port)
    delay = 1.0 / freq if freq > 0 else 0.1
    for i in range(count):
        # generate synthetic accel data
        x = random.uniform(-2, 2)
        y = random.uniform(-2, 2)
        z = random.uniform(-2, 2)
        client.send_message('/accelerometer', [x, y, z])
        print(f"sent: {x:.3f}, {y:.3f}, {z:.3f}")
        await asyncio.sleep(delay)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9000)
    parser.add_argument('--freq', type=float, default=2.0)
    parser.add_argument('--count', type=int, default=50)
    args = parser.parse_args()
    asyncio.run(run(args.host, args.port, args.freq, args.count))
