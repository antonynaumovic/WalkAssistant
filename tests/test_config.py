import unittest
from config import WalkAssistantConfig
import os
import tempfile


class TestWalkAssistantConfig(unittest.TestCase):
    def setUp(self):
        self.tempfile = tempfile.NamedTemporaryFile(delete=False, suffix=".yaml")
        self.config = WalkAssistantConfig(self.tempfile.name)

    def tearDown(self):
        self.tempfile.close()
        os.unlink(self.tempfile.name)

    def test_default_values(self):
        self.assertIn("bind_address", self.config._WalkAssistantConfig__config)
        self.assertIn("logging_level", self.config._WalkAssistantConfig__config)

    def test_set_and_get(self):
        self.config.set("bind_address", "127.0.0.1")
        self.assertEqual(self.config.config("bind_address"), "127.0.0.1")

    def test_logging_level(self):
        self.config.set("logging_level", "DEBUG")
        self.assertEqual(self.config.config("logging_level"), "DEBUG")


if __name__ == "__main__":
    unittest.main()
