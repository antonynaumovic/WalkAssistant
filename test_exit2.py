import asyncio
import main as m

class FakeWindow:
    async def close(self):
        print("FakeWindow.close started")
        await asyncio.sleep(0)
        print("FakeWindow.close finished")

class FakePage:
    def __init__(self):
        self.window = FakeWindow()
    def add(self, control):
        print(f"FakePage.add called with: {control}")

# Inject fake page into main module
m.p = FakePage()

async def runner():
    # Call the async helper directly (as if scheduled on the UI loop)
    await m._exit_app(None, None)
    # wait a tiny bit to allow the scheduled close task to run
    await asyncio.sleep(0.05)

print("Running test runner...")
asyncio.run(runner())
print("Runner finished")
