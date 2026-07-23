"""Tests for `ScheduledTrigger`: interval x check x target, driven by `FakeClock`."""

from __future__ import annotations

import pytest

from harness.drivers.memory import FakeClock
from harness.drivers.scheduled_trigger import ScheduledTrigger
from harness.ports.triggers import Check, Observation


class FakeCheck(Check):
    """A `Check` returning a configurable, fixed list of observations."""

    def __init__(self, observations: list[Observation]) -> None:
        self._observations = observations

    def evaluate(self) -> list[Observation]:
        return list(self._observations)


# An hour-aligned start so t0 and t0+1800s share a bucket, t0+3600s does not.
T0 = "2026-07-19T10:00:00Z"
T_HALF = "2026-07-19T10:30:00Z"
T_NEXT = "2026-07-19T11:00:00Z"


def test_fires_once_per_interval_bucket() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="hourly",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation()]),
        workflow="wf",
    )

    first = trigger.poll()
    assert len(first) == 1
    task = first[0]
    assert task.workflow_template == "wf"
    assert task.step is None
    assert "source" not in task.data
    assert task.dedup_key is not None

    clock.instant = T_HALF
    assert trigger.poll() == []  # same bucket

    clock.instant = T_NEXT
    second = trigger.poll()
    assert len(second) == 1
    assert second[0].dedup_key != task.dedup_key  # next bucket, different key


def test_step_target() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="cleanup",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation()]),
        step="cleanup",
    )

    task = trigger.poll()[0]
    assert task.step == "cleanup"
    assert task.workflow_template is None


def test_per_state_dedup_stable_across_buckets() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="watch",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation(state_key="abc")]),
        workflow="wf",
        dedup="per-state",
    )

    first = trigger.poll()[0]
    clock.instant = T_NEXT
    second = trigger.poll()[0]
    assert first.dedup_key == second.dedup_key  # same state_key, ignore bucket


def test_per_state_dedup_changes_with_state() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="watch",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation(state_key="abc")]),
        workflow="wf",
        dedup="per-state",
    )
    key_abc = trigger.poll()[0].dedup_key

    clock2 = FakeClock(T0)
    trigger2 = ScheduledTrigger(
        name="watch",
        clock=clock2,
        interval=3600,
        check=FakeCheck([Observation(state_key="xyz")]),
        workflow="wf",
        dedup="per-state",
    )
    key_xyz = trigger2.poll()[0].dedup_key
    assert key_abc != key_xyz


def test_per_state_requires_state_key() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="watch",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation(state_key=None)]),
        workflow="wf",
        dedup="per-state",
    )
    with pytest.raises(ValueError):
        trigger.poll()


def test_observation_data_merged_into_task() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="hourly",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation(data={"title": "x"})]),
        workflow="wf",
    )
    task = trigger.poll()[0]
    assert task.data["title"] == "x"


def test_sink_is_stamped_into_task_data() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="notify",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation()]),
        workflow="wf",
        sink={"kind": "slack"},
    )

    task = trigger.poll()[0]
    assert task.data["sink"] == {"kind": "slack"}
    assert "source" not in task.data  # destination identity only, no origin


def test_none_or_absent_sink_stamps_nothing() -> None:
    for sink in (None, {"kind": "none"}):
        clock = FakeClock(T0)
        trigger = ScheduledTrigger(
            name="quiet",
            clock=clock,
            interval=3600,
            check=FakeCheck([Observation()]),
            workflow="wf",
            sink=sink,
        )

        task = trigger.poll()[0]
        assert "sink" not in task.data


def test_observation_data_cannot_overwrite_the_sink_stamp() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="notify",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation(data={"sink": {"kind": "github"}})]),
        workflow="wf",
        sink={"kind": "slack"},
    )

    task = trigger.poll()[0]
    assert task.data["sink"] == {"kind": "slack"}  # the stamp lands after the merge


def test_requires_exactly_one_target() -> None:
    clock = FakeClock(T0)
    with pytest.raises(ValueError):
        ScheduledTrigger(
            name="bad",
            clock=clock,
            interval=3600,
            check=FakeCheck([Observation()]),
            workflow="wf",
            step="cleanup",
        )
    with pytest.raises(ValueError):
        ScheduledTrigger(
            name="bad",
            clock=clock,
            interval=3600,
            check=FakeCheck([Observation()]),
        )


def test_rejects_unknown_dedup() -> None:
    clock = FakeClock(T0)
    with pytest.raises(ValueError):
        ScheduledTrigger(
            name="bad",
            clock=clock,
            interval=3600,
            check=FakeCheck([Observation()]),
            workflow="wf",
            dedup="bogus",
        )


def test_observation_repository_overrides_trigger_repository() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="multi",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation(state_key="a", repository="heblo")]),
        workflow="wf",
        repository="fallback",
        dedup="per-state",
    )

    task = trigger.poll()[0]
    assert task.repository == "heblo"


def test_task_repository_falls_back_to_trigger_when_observation_has_none() -> None:
    clock = FakeClock(T0)
    trigger = ScheduledTrigger(
        name="single",
        clock=clock,
        interval=3600,
        check=FakeCheck([Observation()]),
        workflow="wf",
        repository="fallback",
    )

    task = trigger.poll()[0]
    assert task.repository == "fallback"
