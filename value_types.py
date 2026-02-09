from aenum import Enum


class WalkAssistantValueTypes(Enum):
    _init_ = "value string bind"
    FLOAT = "float", "Float", "X"
    INT = "int", "Int", "X"
    BOOL = "bool", "Boolean", "X"
    STRING = "string", "String", "X"
    VECTOR2 = "vector2", "Vector 2", "XY"
    VECTOR3 = "vector3", "Vector 3", "XYZ"
    VECTOR4 = "vector4", "Vector 4", "XYZW"

    def __str__(self):
        return self.string
