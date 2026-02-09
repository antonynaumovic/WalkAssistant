import sys
import os

# ensure the project root is on sys.path so local modules can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from value_types import WalkAssistantValueTypes


def main():
    try:
        assert WalkAssistantValueTypes.FLOAT.name == "FLOAT"
        assert WalkAssistantValueTypes.FLOAT.value == "float"
        assert WalkAssistantValueTypes.VECTOR3.name == "VECTOR3"
        assert WalkAssistantValueTypes.VECTOR3.value == "vector3"
    except AssertionError:
        import traceback

        traceback.print_exc()
        print("Simple test failed")
        return 1
    print("Simple test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
