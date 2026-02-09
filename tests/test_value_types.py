import unittest
from value_types import WalkAssistantValueTypes


class TestWalkAssistantValueTypes(unittest.TestCase):
    def test_enum_names(self):
        self.assertIn("VECTOR3", WalkAssistantValueTypes.__members__)
        self.assertEqual(WalkAssistantValueTypes.VECTOR3.value, "vector3")

    def test_enum_string(self):
        self.assertEqual(
            WalkAssistantValueTypes.VECTOR3.string.replace(" ", ""), "Vector3"
        )


if __name__ == "__main__":
    unittest.main()
