from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from harness.drivers.checks import (
    BUILTIN_CHECKS,
    AlwaysCheck,
    CommandCheck,
    DiskThresholdCheck,
    FileGlobCheck,
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


def test_file_glob_fires_one_observation_per_file() -> None:
    check = FileGlobCheck(
        path="/watch",
        pattern="*.txt",
        lister=lambda path, pattern: ["/watch/a.txt", "/watch/b.txt"],
    )
    result = check.evaluate()
    assert len(result) == 2
    assert result[0].state_key == "/watch/a.txt"
    assert result[0].data == {"title": "file /watch/a.txt", "file": "/watch/a.txt"}
    assert result[1].state_key == "/watch/b.txt"


def test_file_glob_empty_listing_does_not_fire() -> None:
    check = FileGlobCheck(path="/watch", lister=lambda path, pattern: [])
    assert check.evaluate() == []


def test_file_glob_default_lister_matches_files_on_disk(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.md").write_text("b")
    result = FileGlobCheck(path=str(tmp_path), pattern="*.txt").evaluate()
    assert [obs.state_key for obs in result] == [str(tmp_path / "a.txt")]
    missing = FileGlobCheck(path=str(tmp_path / "nowhere"))
    assert missing.evaluate() == []


def _completed(*, returncode: int, stdout: str):
    return lambda command, timeout: SimpleNamespace(
        returncode=returncode, stdout=stdout
    )


def test_command_fires_per_non_empty_stdout_line() -> None:
    check = CommandCheck(
        command="ls", runner=_completed(returncode=0, stdout="one\n\n  two  \n")
    )
    result = check.evaluate()
    assert len(result) == 2
    assert result[0].state_key == "one"
    assert result[0].data == {"title": "one"}
    assert result[1].state_key == "two"


def test_command_non_zero_exit_does_not_fire() -> None:
    check = CommandCheck(
        command="false", runner=_completed(returncode=1, stdout="ignored\n")
    )
    assert check.evaluate() == []


def test_command_timeout_does_not_fire() -> None:
    def raising(command: str, timeout: float):
        raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)

    check = CommandCheck(command="sleep 99", runner=raising)
    assert check.evaluate() == []


def test_command_default_runner_runs_a_real_command() -> None:
    result = CommandCheck(command="echo hello").evaluate()
    assert [obs.state_key for obs in result] == ["hello"]


def test_builtin_fs_files_factory_builds_glob_check_with_default_pattern() -> None:
    check = BUILTIN_CHECKS["fs-files"]({"path": "/watch"})
    assert isinstance(check, FileGlobCheck)
    assert check._pattern == "*"


def test_builtin_command_factory_builds_command_check_with_default_timeout() -> None:
    check = BUILTIN_CHECKS["command"]({"command": "echo hi"})
    assert isinstance(check, CommandCheck)
    assert check._timeout == 30.0


def test_builtin_factories_raise_key_error_on_missing_required_param() -> None:
    with pytest.raises(KeyError):
        BUILTIN_CHECKS["fs-files"]({})
    with pytest.raises(KeyError):
        BUILTIN_CHECKS["command"]({})
