from aenum import Enum


class WalkAssistantValueTypes(Enum):
    _init_ = "value string"
    FLOAT = "float", "Float"
    INT = "int", "Int"
    BOOL = "bool", "Boolean"
    STRING = "string", "String"
    VECTOR2 = "vector2", "Vector 2"
    VECTOR3 = "vector3", "Vector 3"
    VECTOR4 = "vector4", "Vector 4"
