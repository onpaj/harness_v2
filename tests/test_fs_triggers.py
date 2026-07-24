"""`FilesystemTriggerRepository`: `triggers/*.json` → `ScheduledTrigger`s."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.drivers.fs_triggers import FilesystemTriggerRepository, TriggerValidationError
from harness.drivers.scheduled_trigger import ScheduledTrigger
from harness.drivers.system_clock import SystemClock


def _write(root: Path, name: str, body: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


def test_valid_always_workflow_file_builds_one_trigger(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "nightly",
        {
            "kind": "scheduled",
            "interval": "1h",
            "check": "always",
            "target": {"workflow": "wf"},
        },
    )

    triggers = FilesystemTriggerRepository(tmp_path).build(clock=SystemClock())

    assert len(triggers) == 1
    (trigger,) = triggers
    assert isinstance(trigger, ScheduledTrigger)
    assert trigger.kind == "scheduled:nightly"
    assert trigger._interval == 3600.0
    assert trigger._workflow == "wf"
    assert trigger._step is None


def test_disk_threshold_step_file_builds_a_working_trigger(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "disk-pressure",
        {
            "kind": "scheduled",
            "interval": "1h",
            "check": "disk-threshold",
            "params": {"path": "/", "percent": 80},
            "target": {"step": "cleanup"},
            "dedup": "per-state",
        },
    )

    triggers = FilesystemTriggerRepository(tmp_path).build(clock=SystemClock())

    assert len(triggers) == 1
    (trigger,) = triggers
    assert trigger.kind == "scheduled:disk-pressure"
    assert trigger._step == "cleanup"
    assert trigger._workflow is None


def test_name_key_overrides_file_stem(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "on-disk-stem",
        {
            "kind": "scheduled",
            "name": "chosen-name",
            "interval": "30m",
            "check": "always",
            "target": {"workflow": "wf"},
        },
    )

    (trigger,) = FilesystemTriggerRepository(tmp_path).build(clock=SystemClock())

    assert trigger.kind == "scheduled:chosen-name"


def test_missing_directory_returns_empty_list(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    assert FilesystemTriggerRepository(missing).build(clock=SystemClock()) == []


def _build(root: Path, **kwargs) -> list[ScheduledTrigger]:
    return FilesystemTriggerRepository(root).build(clock=SystemClock(), **kwargs)


def test_bad_interval_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad-interval",
        {
            "kind": "scheduled",
            "interval": "1x",
            "check": "always",
            "target": {"workflow": "wf"},
        },
    )

    with pytest.raises(TriggerValidationError) as excinfo:
        _build(tmp_path)
    assert "bad-interval" in str(excinfo.value)


def test_unknown_check_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad-check",
        {
            "kind": "scheduled",
            "interval": "1h",
            "check": "nope",
            "target": {"workflow": "wf"},
        },
    )

    with pytest.raises(TriggerValidationError) as excinfo:
        _build(tmp_path)
    assert "bad-check" in str(excinfo.value)


def test_missing_target_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "no-target",
        {"kind": "scheduled", "interval": "1h", "check": "always"},
    )

    with pytest.raises(TriggerValidationError) as excinfo:
        _build(tmp_path)
    assert "no-target" in str(excinfo.value)


def test_both_targets_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "both-targets",
        {
            "kind": "scheduled",
            "interval": "1h",
            "check": "always",
            "target": {"workflow": "wf", "step": "cleanup"},
        },
    )

    with pytest.raises(TriggerValidationError) as excinfo:
        _build(tmp_path)
    assert "both-targets" in str(excinfo.value)


def test_unknown_dedup_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad-dedup",
        {
            "kind": "scheduled",
            "interval": "1h",
            "check": "always",
            "target": {"workflow": "wf"},
            "dedup": "per-eternity",
        },
    )

    with pytest.raises(TriggerValidationError) as excinfo:
        _build(tmp_path)
    assert "bad-dedup" in str(excinfo.value)


def test_missing_kind_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "no-kind",
        {
            "interval": "1h",
            "check": "always",
            "target": {"workflow": "wf"},
        },
    )

    with pytest.raises(TriggerValidationError) as excinfo:
        _build(tmp_path)
    assert "no-kind" in str(excinfo.value)


def test_target_outside_known_targets_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "unknown-wf",
        {
            "kind": "scheduled",
            "interval": "1h",
            "check": "always",
            "target": {"workflow": "other"},
        },
    )

    with pytest.raises(TriggerValidationError) as excinfo:
        _build(tmp_path, known_targets={"wf"})
    assert "unknown-wf" in str(excinfo.value)


# --- cron cadence -------------------------------------------------------------


def test_valid_cron_file_builds_one_trigger(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "weekly-review",
        {
            "kind": "scheduled",
            "cron": "0 6 * * 1",
            "check": "always",
            "target": {"workflow": "wf"},
        },
    )

    (trigger,) = FilesystemTriggerRepository(tmp_path).build(clock=SystemClock())

    assert trigger.kind == "scheduled:weekly-review"
    assert trigger._interval is None
    assert trigger._cron is not None
    assert trigger._workflow == "wf"


def test_bad_cron_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad-cron",
        {
            "kind": "scheduled",
            "cron": "0 6 31 2 *",
            "check": "always",
            "target": {"workflow": "wf"},
        },
    )

    with pytest.raises(TriggerValidationError) as excinfo:
        _build(tmp_path)
    assert "bad-cron" in str(excinfo.value)


def test_both_interval_and_cron_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "both-cadences",
        {
            "kind": "scheduled",
            "interval": "1h",
            "cron": "0 6 * * 1",
            "check": "always",
            "target": {"workflow": "wf"},
        },
    )

    with pytest.raises(TriggerValidationError) as excinfo:
        _build(tmp_path)
    assert "both-cadences" in str(excinfo.value)


def test_neither_interval_nor_cron_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "no-cadence",
        {
            "kind": "scheduled",
            "check": "always",
            "target": {"workflow": "wf"},
        },
    )

    with pytest.raises(TriggerValidationError) as excinfo:
        _build(tmp_path)
    assert "no-cadence" in str(excinfo.value)
