import main as m

print("Calling exit_app() synchronously...")
try:
    m.exit_app()
    print("exit_app() returned without creating coroutine.")
except Exception as e:
    print("exit_app() raised:", e)
