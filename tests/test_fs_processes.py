"""`FilesystemProcessRepository`: `processes/*.json` → `ScheduledTrigger`s.

A Process is a compile-time authoring aggregate — a nested
trigger/action/target/sink shape — that compiles to the same `ScheduledTrigger`
a bare `triggers/*.json` file does. These tests exercise the schema shape and
its fail-fast validation; the trigger's runtime behaviour (clock-gate, dedup) is
covered by `test_scheduled_trigger.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.drivers.fs_processes import (
    FilesystemProcessRepository,
    ProcessValidationError,
)
from harness.drivers.scheduled_trigger import ScheduledTrigger
from harness.drivers.system_clock import SystemClock


def _write(root: Path, name: str, body: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


def _build(root: Path, **kwargs) -> list[ScheduledTrigger]:
    return FilesystemProcessRepository(root).build(clock=SystemClock(), **kwargs)


# --- happy paths ------------------------------------------------------------


def test_valid_always_workflow_process_builds_one_trigger(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "nightly",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
            "sink": {"kind": "none"},
        },
    )

    triggers = _build(tmp_path)

    assert len(triggers) == 1
    (trigger,) = triggers
    assert isinstance(trigger, ScheduledTrigger)
    assert trigger.kind == "scheduled:nightly"
    assert trigger._interval == 3600.0
    assert trigger._workflow == "wf"
    assert trigger._step is None
    # It really produces a task on a fresh bucket, wired to the workflow.
    (task,) = trigger.poll()
    assert task.workflow_template == "wf"
    assert task.step is None
    assert "source" not in task.data  # a Process reflects nothing outward in v1


def test_disk_threshold_step_process_builds_a_working_trigger(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "disk-pressure",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "disk-threshold", "params": {"path": "/", "percent": 80}},
            "target": {"step": "cleanup"},
            "dedup": "per-state",
        },
    )

    (trigger,) = _build(tmp_path)

    assert trigger.kind == "scheduled:disk-pressure"
    assert trigger._step == "cleanup"
    assert trigger._workflow is None
    assert trigger._dedup == "per-state"


def test_name_key_overrides_file_stem(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "on-disk-stem",
        {
            "name": "chosen-name",
            "trigger": {"interval": "30m"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
        },
    )

    (trigger,) = _build(tmp_path)

    assert trigger.kind == "scheduled:chosen-name"


def test_sink_absent_is_accepted(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "no-sink",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
        },
    )

    assert len(_build(tmp_path)) == 1


def test_sink_none_or_absent_stamps_no_data_sink(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "explicit-none",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
            "sink": {"kind": "none"},
        },
    )
    _write(
        tmp_path,
        "no-sink",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
        },
    )

    for trigger in _build(tmp_path):
        (task,) = trigger.poll()
        assert "sink" not in task.data


def test_slack_sink_is_accepted_and_stamped_onto_tasks(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "notify",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
            "sink": {"kind": "slack"},
        },
    )

    (trigger,) = _build(tmp_path)

    assert trigger.sink == {"kind": "slack"}
    (task,) = trigger.poll()
    assert task.data["sink"] == {"kind": "slack"}
    assert "source" not in task.data  # destination identity, no origin stamp


def test_github_sink_is_accepted_and_stamped_onto_tasks(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "notify-github",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
            "sink": {"kind": "github"},
        },
    )

    (trigger,) = _build(tmp_path)

    assert trigger.sink == {"kind": "github"}
    (task,) = trigger.poll()
    assert task.data["sink"] == {"kind": "github"}
    assert "source" not in task.data  # destination identity, no origin stamp


def test_trigger_kind_schedule_is_accepted(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "explicit-kind",
        {
            "trigger": {"kind": "schedule", "interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
        },
    )

    assert len(_build(tmp_path)) == 1


def test_missing_directory_returns_empty_list(tmp_path: Path) -> None:
    assert _build(tmp_path / "does-not-exist") == []


# --- fail-fast validation ---------------------------------------------------


def test_broken_json_raises_naming_the_file(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "broken" in str(excinfo.value)


def test_non_object_raises_naming_the_file(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "a-list.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "a-list" in str(excinfo.value)


def test_missing_trigger_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "no-trigger",
        {"action": {"check": "always"}, "target": {"workflow": "wf"}},
    )

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "no-trigger" in str(excinfo.value)


def test_bad_interval_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad-interval",
        {
            "trigger": {"interval": "1x"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
        },
    )

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "bad-interval" in str(excinfo.value)


def test_missing_action_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "no-action",
        {"trigger": {"interval": "1h"}, "target": {"workflow": "wf"}},
    )

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "no-action" in str(excinfo.value)


def test_unknown_check_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad-check",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "nope"},
            "target": {"workflow": "wf"},
        },
    )

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "bad-check" in str(excinfo.value)


def test_missing_target_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "no-target",
        {"trigger": {"interval": "1h"}, "action": {"check": "always"}},
    )

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "no-target" in str(excinfo.value)


def test_both_targets_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "both-targets",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "wf", "step": "cleanup"},
        },
    )

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "both-targets" in str(excinfo.value)


def test_target_outside_known_targets_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "unknown-wf",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "other"},
        },
    )

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path, known_targets={"wf"})
    assert "unknown-wf" in str(excinfo.value)


def test_unknown_dedup_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "bad-dedup",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
            "dedup": "per-eternity",
        },
    )

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "bad-dedup" in str(excinfo.value)


def test_unknown_sink_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "teams-sink",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
            "sink": {"kind": "teams"},
        },
    )

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "teams-sink" in str(excinfo.value)
    assert excinfo.value.field == "sink"


def test_unknown_trigger_kind_raises_naming_the_file(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "webhook-trigger",
        {
            "trigger": {"kind": "webhook", "interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "wf"},
        },
    )

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "webhook-trigger" in str(excinfo.value)
    assert excinfo.value.field == "trigger"


def test_disk_threshold_missing_params_raises_process_error_not_keyerror(
    tmp_path: Path,
) -> None:
    # The `disk-threshold` factory reads `params["path"]`; a file missing it used
    # to surface a raw KeyError from the factory. `compile_process` now wraps the
    # factory call, so the build fails as a ProcessValidationError naming the file.
    _write(
        tmp_path,
        "no-params",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "disk-threshold"},
            "target": {"step": "cleanup"},
        },
    )

    with pytest.raises(ProcessValidationError) as excinfo:
        _build(tmp_path)
    assert "no-params" in str(excinfo.value)
