import unittest
import importlib


class TestMainIntegration(unittest.TestCase):
    def test_import_main(self):
        # Just ensure main.py can be imported without error
        importlib.import_module("main")


if __name__ == "__main__":
    unittest.main()
