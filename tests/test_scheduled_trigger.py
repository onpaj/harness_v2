"""Tests for `ScheduledTrigger`: interval x check x target, driven by `FakeClock`."""

from __future__ import annotations

import pytest

from harness.drivers.memory import FakeClock, MemoryEventSink, MemoryTaskQueue
from harness.drivers.scheduled_trigger import ScheduledTrigger
from harness.ports.triggers import Check, Observation, parse_cron
from harness.source_poller import SourcePoller


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


# --- cron cadence: the same guarantees interval mode has, one shared seam ---

WEEKLY_MONDAY_6AM = parse_cron("0 6 * * 1")
CRON_T0 = "2026-07-20T06:00:00Z"  # a Monday, exactly on the occurrence
CRON_T_HALF = "2026-07-20T10:00:00Z"  # later the same day, same occurrence
CRON_T_NEXT = "2026-07-27T06:00:00Z"  # the following Monday, a new occurrence


def test_fires_once_per_cron_occurrence() -> None:
    clock = FakeClock(CRON_T0)
    trigger = ScheduledTrigger(
        name="weekly",
        clock=clock,
        cron=WEEKLY_MONDAY_6AM,
        check=FakeCheck([Observation()]),
        workflow="wf",
    )

    first = trigger.poll()
    assert len(first) == 1
    task = first[0]
    assert task.workflow_template == "wf"
    assert "source" not in task.data
    assert task.dedup_key is not None

    clock.instant = CRON_T_HALF
    assert trigger.poll() == []  # same occurrence, no re-fire

    clock.instant = CRON_T_NEXT
    second = trigger.poll()
    assert len(second) == 1
    assert second[0].dedup_key != task.dedup_key  # next occurrence, different key


def test_cron_requires_a_matching_moment_before_first_firing() -> None:
    # Starting mid-week, before the week's occurrence has happened *this* week
    # is irrelevant to the gate: occurrence_at_or_before always finds the most
    # recent past occurrence, so the trigger still fires on the very first poll.
    clock = FakeClock("2026-07-22T12:00:00Z")  # a Wednesday
    trigger = ScheduledTrigger(
        name="weekly",
        clock=clock,
        cron=WEEKLY_MONDAY_6AM,
        check=FakeCheck([Observation()]),
        workflow="wf",
    )

    assert len(trigger.poll()) == 1
    assert trigger.poll() == []  # still the same (already-consumed) occurrence


def test_requires_exactly_one_of_interval_or_cron() -> None:
    clock = FakeClock(CRON_T0)
    with pytest.raises(ValueError):
        ScheduledTrigger(
            name="bad",
            clock=clock,
            check=FakeCheck([Observation()]),
            workflow="wf",
            interval=3600,
            cron=WEEKLY_MONDAY_6AM,
        )
    with pytest.raises(ValueError):
        ScheduledTrigger(
            name="bad",
            clock=clock,
            check=FakeCheck([Observation()]),
            workflow="wf",
        )


def test_cron_per_state_dedup_stable_across_occurrences() -> None:
    # per-state dedup is cadence-blind: cadence only decides *when* the check
    # runs, never what the dedup key is keyed on.
    clock = FakeClock(CRON_T0)
    trigger = ScheduledTrigger(
        name="watch",
        clock=clock,
        cron=WEEKLY_MONDAY_6AM,
        check=FakeCheck([Observation(state_key="abc")]),
        workflow="wf",
        dedup="per-state",
    )

    first = trigger.poll()[0]
    clock.instant = CRON_T_NEXT
    second = trigger.poll()[0]
    assert first.dedup_key == second.dedup_key  # same state_key, ignore occurrence


def test_cron_restart_mid_occurrence_does_not_refire() -> None:
    """The cron twin of the interval restart guarantee: `SourcePoller._seen`,
    seeded from a previously emitted task's persisted `dedup_key`, suppresses a
    re-fire when a fresh trigger/poller pair (simulating a process restart)
    polls again inside the same occurrence."""
    clock = FakeClock(CRON_T0)
    trigger = ScheduledTrigger(
        name="weekly",
        clock=clock,
        cron=WEEKLY_MONDAY_6AM,
        check=FakeCheck([Observation()]),
        workflow="wf",
    )
    inbox = MemoryTaskQueue("tasks")
    poller = SourcePoller(source=trigger, inbox=inbox, events=MemoryEventSink())

    poller.tick()
    (emitted,) = inbox.list()

    # Simulate a restart: a fresh trigger and a fresh poller, seeded from disk.
    clock_after_restart = FakeClock(CRON_T_HALF)  # still inside the same occurrence
    trigger_after_restart = ScheduledTrigger(
        name="weekly",
        clock=clock_after_restart,
        cron=WEEKLY_MONDAY_6AM,
        check=FakeCheck([Observation()]),
        workflow="wf",
    )
    inbox_after_restart = MemoryTaskQueue("tasks")
    poller_after_restart = SourcePoller(
        source=trigger_after_restart, inbox=inbox_after_restart, events=MemoryEventSink()
    )
    poller_after_restart.seed([emitted])

    poller_after_restart.tick()
    assert inbox_after_restart.list() == []
