import enum


class TestResult(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    DISCARD = "DISCARD"