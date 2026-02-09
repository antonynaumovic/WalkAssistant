import unittest
from osc_server import create_handlers, get_group_outputs, set_rate_limit


class TestOscServer(unittest.TestCase):
    def test_create_handlers_and_outputs(self):
        groups = [
            {
                "alias": "TestGroup",
                "value_type": "vector3",
                "endpoints": [
                    {"resource": "/test", "value_type": "vector3", "bind": "xyz"}
                ],
            }
        ]
        create_handlers(groups)
        outputs = get_group_outputs()
        self.assertTrue(any(o["label"] == "TestGroup" for o in outputs))

    def test_rate_limit(self):
        set_rate_limit(10)
        # No assertion, just ensure no exception


if __name__ == "__main__":
    unittest.main()
