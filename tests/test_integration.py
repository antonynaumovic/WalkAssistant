import unittest
from unittest.mock import MagicMock, patch
import asyncio
import time
import os

# Set PYTHONPATH to project root for imports to work
import sys

sys.path.append(os.getcwd())

import osc_server


class TestOSCIntegration(unittest.TestCase):
    def test_message_callback_triggered(self):
        """Test that registering a callback and calling the handler triggers the callback."""
        mock_cb = MagicMock()
        osc_server.register_message_callback(mock_cb)

        # Simulate an incoming OSC message
        # acceleration_handler(addr, x, y, z)
        osc_server.acceleration_handler("/accelerometer", 1.0, 2.0, 3.0)

        # Check if callback was called
        self.assertTrue(mock_cb.called)
        args, kwargs = mock_cb.call_args
        msg = args[0]
        self.assertEqual(msg["endpoint"], "/accelerometer")
        self.assertEqual(msg["x"], 1.0)
        self.assertEqual(msg["y"], 2.0)
        self.assertEqual(msg["z"], 3.0)
        self.assertIn("smoothed", msg)

        osc_server.unregister_message_callback(mock_cb)

    def test_message_notification_handles_error(self):
        """Test that if a callback raises an error, it doesn't crash the server."""
        bad_cb = MagicMock(side_effect=Exception("Boom"))
        osc_server.register_message_callback(bad_cb)

        # Should not raise exception
        osc_server.acceleration_handler("/accelerometer", 1.0, 1.0, 1.0)

        self.assertTrue(bad_cb.called)
        osc_server.unregister_message_callback(bad_cb)

    def test_osc_server_start_stop_notifications(self):
        """Test that starting/stopping the server notifies status callbacks."""
        mock_status_cb = MagicMock()
        osc_server.register_status_callback(mock_status_cb)

        # Simulate server start (manually call _notify_status since we don't want to actually start the network server)
        osc_server._notify_status(True)
        mock_status_cb.assert_called_with(True)

        osc_server._notify_status(False)
        mock_status_cb.assert_called_with(False)

        osc_server.unregister_status_callback(mock_status_cb)

    def test_osc_server_ip_notification(self):
        """Test that IP changes notify callbacks."""
        mock_ip_cb = MagicMock()
        osc_server.register_ip_callback(mock_ip_cb)

        osc_server._notify_ip("127.0.0.1:9000")
        mock_ip_cb.assert_called_with("127.0.0.1:9000")

        osc_server.unregister_ip_callback(mock_ip_cb)


if __name__ == "__main__":
    unittest.main()
