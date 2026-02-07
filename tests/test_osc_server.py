import unittest
import asyncio
import time
from unittest.mock import MagicMock
import osc_server


class TestOSCServer(unittest.TestCase):
    def setUp(self):
        # Reset globals in osc_server for each test if possible,
        # but since they are module-level, we might need to be careful.
        osc_server._status_callbacks = []
        osc_server._message_callbacks = []
        osc_server._ip_callbacks = []
        osc_server.server_transport = None
        osc_server.server_protocol = None
        osc_server.ip_str = None

    def test_registration_callbacks(self):
        cb = MagicMock()
        osc_server.register_status_callback(cb)
        self.assertIn(cb, osc_server._status_callbacks)

        osc_server.unregister_status_callback(cb)
        self.assertNotIn(cb, osc_server._status_callbacks)

        osc_server.register_ip_callback(cb)
        self.assertIn(cb, osc_server._ip_callbacks)

        osc_server.unregister_ip_callback(cb)
        self.assertNotIn(cb, osc_server._ip_callbacks)

        osc_server.register_message_callback(cb)
        self.assertIn(cb, osc_server._message_callbacks)

        osc_server.unregister_message_callback(cb)
        self.assertNotIn(cb, osc_server._message_callbacks)

    def test_acceleration_handler_calculates_correctly(self):
        # We need to set smoothing to 0 to easily check magnitude
        osc_server.set_smoothing(0.0)

        received_msgs = []

        def cb(msg):
            received_msgs.append(msg)

        osc_server.register_message_callback(cb)

        # Test values: 3, 4, 0 -> magnitude 3^2 + 4^2 + 0^2 = 25
        osc_server.acceleration_handler("/accelerometer", 3.0, 4.0, 0.0)

        self.assertEqual(len(received_msgs), 1)
        self.assertEqual(received_msgs[0]["magnitude"], 25.0)
        self.assertEqual(received_msgs[0]["smoothed"], 25.0)
        self.assertEqual(received_msgs[0]["x"], 3.0)
        self.assertEqual(received_msgs[0]["y"], 4.0)
        self.assertEqual(received_msgs[0]["z"], 0.0)

    def test_acceleration_handler_smoothing(self):
        osc_server.set_smoothing(0.5)
        osc_server.last_acceleration_smoothed = 10.0

        received_msgs = []
        osc_server.register_message_callback(lambda m: received_msgs.append(m))

        # magnitude = 20
        # smoothed = 10 * 0.5 + 20 * (1 - 0.5) = 5 + 10 = 15
        osc_server.acceleration_handler("/accelerometer", 0.0, 0.0, 20.0**0.5)

        self.assertAlmostEqual(received_msgs[0]["smoothed"], 15.0)

    def test_get_ip_string_bug(self):
        # Current implementation: return ip_str is not None (bool)
        # Expected: returns the actual string or None
        osc_server.ip_str = "127.0.0.1:9000"
        self.assertEqual(osc_server.get_ip_string(), "127.0.0.1:9000")

    def test_is_running(self):
        osc_server.server_transport = None
        self.assertFalse(osc_server.is_running())
        osc_server.server_transport = object()  # mock transport
        self.assertTrue(osc_server.is_running())

    async def async_test_server_lifecycle(self):
        # We need a free port
        port = 9999
        addr = "127.0.0.1"
        endpoint = "/test"

        # Register status callback
        status_changes = []
        osc_server.register_status_callback(lambda s: status_changes.append(s))

        # Register IP callback
        ips = []
        osc_server.register_ip_callback(lambda ip: ips.append(ip))

        # Start server in a task
        task = asyncio.create_task(
            osc_server.start_async_osc_server(addr=addr, port=port, endpoint=endpoint)
        )

        # Wait a bit for server to start
        await asyncio.sleep(0.1)

        self.assertTrue(osc_server.is_running())
        self.assertIn(True, status_changes)
        self.assertEqual(osc_server.ip_str, f"{addr}:{port}")
        self.assertIn(f"{addr}:{port}", ips)

        # Cancel task to stop server
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        await asyncio.sleep(0.1)
        self.assertFalse(osc_server.is_running())
        self.assertIn(False, status_changes)
        self.assertEqual(osc_server.ip_str, None)
        self.assertEqual(osc_server.get_ip_string(), None)
        self.assertIn("Server not running", ips)

    def test_server_lifecycle(self):
        asyncio.run(self.async_test_server_lifecycle())


if __name__ == "__main__":
    unittest.main()
