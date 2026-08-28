from mnemosyne.readiness import ReadinessCheck


def test_readiness_check_pass() -> None:
    check = ReadinessCheck("example", True, "ok")
    assert check.passed is True


def test_readiness_check_fail() -> None:
    check = ReadinessCheck("example", False, "bad")
    assert check.passed is False
