"""Reference rotary SDK result codes and function identifiers."""
from enum import IntEnum


class Result(IntEnum):
    OK = 0
    INVALID_ARGUMENT = 1
    NOT_ENABLED = 2
    NOT_HOMED = 3
    BUSY = 4
    LIMIT = 5
    FAULT = 6
    UNKNOWN_FUNCTION = 7


FUNCTIONS = ("Enable", "Disable", "Home", "MoveAbsolute", "MoveRelative",
             "Stop", "ClearFault", "GetPosition", "GetState")
CONTROL_ERROR = -(2 ** 31)
