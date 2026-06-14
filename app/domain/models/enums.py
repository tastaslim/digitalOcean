from enum import Enum


class TaskType(str, Enum):
    JOB_CANCELLATION = "JOB_CANCELLATION"
    ORDER_CANCELLATION = "ORDER_CANCELLATION"
    GENERIC = "GENERIC"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EvalStatus(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    SEMANTIC_MATCH = "SEMANTIC_MATCH"
    MISMATCH = "MISMATCH"
    PARSE_ERROR = "PARSE_ERROR"
    ERROR = "ERROR"
