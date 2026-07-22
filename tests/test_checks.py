from __future__ import annotations

from types import SimpleNamespace

from harness.drivers.checks import (
    BUILTIN_CHECKS,
    AlwaysCheck,
    DiskThresholdCheck,
)
from harness.ports.triggers import Observation


def _usage(*, total: int, used: int):
    return lambda path: SimpleNamespace(total=total, used=used, free=total - used)


def test_always_check_fires_one_empty_observation() -> None:
    result = AlwaysCheck().evaluate()
    assert result == [Observation()]
    assert len(result) == 1
    assert result[0].state_key is None


def test_disk_threshold_over_fires_with_state_key() -> None:
    check = DiskThresholdCheck(
        path="/", percent=80, usage=_usage(total=100, used=85)
    )
    result = check.evaluate()
    assert len(result) == 1
    assert result[0].state_key
    assert "disk" in result[0].data["title"]


def test_disk_threshold_under_does_not_fire() -> None:
    check = DiskThresholdCheck(
        path="/", percent=80, usage=_usage(total=100, used=50)
    )
    assert check.evaluate() == []


def test_disk_threshold_zero_total_does_not_fire() -> None:
    check = DiskThresholdCheck(
        path="/", percent=80, usage=_usage(total=0, used=0)
    )
    assert check.evaluate() == []


def test_builtin_always_factory_builds_always_check() -> None:
    check = BUILTIN_CHECKS["always"]({})
    assert isinstance(check, AlwaysCheck)


def test_builtin_disk_threshold_factory_builds_disk_check() -> None:
    check = BUILTIN_CHECKS["disk-threshold"]({"path": "/", "percent": 90})
    assert isinstance(check, DiskThresholdCheck)
    assert isinstance(check.evaluate(), list)
