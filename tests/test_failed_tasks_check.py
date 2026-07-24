"""`FailedTasksCheck` — the self-heal action (ADR-0018).

Migrated from the old `Healer` loop's `tests/test_healer.py`: same behavioral
guarantees (claim + settle exactly once, empty/lost-claim no-ops, the
recursion guard), now exercised directly against the `Check` port.
"""

from harness.drivers.failed_tasks_check import FailedTasksCheck
from harness.drivers.memory import FakeClock, MemoryEventSink, MemoryTaskQueue
from harness.models import FAILED, HEALED, HistoryEntry, Task


def failed_task(
    task_id: str = "tsk_boom",
    *,
    reason: str = "boom",
    data: dict | None = None,
) -> Task:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-21T10:00:00Z",
        repository="app",
        status=FAILED,
        data=data if data is not None else {"request": "Do the thing"},
        history=(
            HistoryEntry(
                at="2026-07-21T10:00:00Z",
                actor="consumer:development",
                from_step="development",
                to_step=FAILED,
                reason=reason,
            ),
        ),
    )


def make_check(*, failed, healed, events=None, clock=None) -> FailedTasksCheck:
    return FailedTasksCheck(
        failed=failed,
        healed=healed,
        events=events or MemoryEventSink(),
        clock=clock or FakeClock(),
    )


def test_empty_failed_queue_is_a_noop():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    check = make_check(failed=failed, healed=healed)

    assert check.evaluate() == []
    assert healed.list() == []


def test_lost_claim_race_is_a_noop():
    class NeverClaims(MemoryTaskQueue):
        def claim(self, task, lock_id):
            return None  # someone else grabbed it first

    failed = NeverClaims("failed")
    failed.put(failed_task())
    healed = MemoryTaskQueue("healed")
    check = make_check(failed=failed, healed=healed)

    assert check.evaluate() == []
    assert healed.list() == []


def test_two_failed_tasks_yield_two_observations_and_drain_failed():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task("tsk_a"))
    failed.put(failed_task("tsk_b"))
    check = make_check(failed=failed, healed=healed)

    observations = check.evaluate()

    assert {obs.state_key for obs in observations} == {"tsk_a", "tsk_b"}
    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 2
    assert all(task.status == HEALED for task in settled)
    assert all("queued for healing" in task.history[-1].summary for task in settled)


def test_observation_carries_the_recursion_guard_marker():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task("tsk_boom"))
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    assert observation.data["heal"] == {"of": "tsk_boom"}


def test_observation_body_contains_the_rendered_failure_report():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task(reason="no edge from review"))
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    body = observation.data["body"]
    assert "tsk_boom" in body
    assert "no edge from review" in body
    assert "## Failure report" in body
    # structured fields are present *alongside* the rendered body, not instead
    assert observation.data["reason"] == "no edge from review"
    assert observation.data["history"] == []


def test_observation_request_is_synthesized_distinct_from_original_request():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task())
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    assert observation.data["original_request"] == "Do the thing"
    assert observation.data["request"] != "Do the thing"
    assert "tsk_boom" in observation.data["request"]


def test_observation_carries_source_when_the_original_task_has_one():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task(data={"request": "x", "source": {"url": "https://gh/i/1"}}))
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    assert observation.data["source"] == {"url": "https://gh/i/1"}


def test_observation_omits_source_when_the_original_task_has_none():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task())
    check = make_check(failed=failed, healed=healed)

    (observation,) = check.evaluate()

    assert "source" not in observation.data


def test_recursion_guard_skips_a_heal_task_and_settles_without_an_observation():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task("tsk_heal_1", data={"heal": {"of": "tsk_boom"}}))
    check = make_check(failed=failed, healed=healed)

    observations = check.evaluate()

    assert observations == []
    assert failed.list() == []
    settled = healed.list()
    assert len(settled) == 1
    assert settled[0].status == HEALED
    assert "heal-failed" in settled[0].history[-1].summary


def test_healing_and_healed_events_are_emitted():
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    failed.put(failed_task())
    events = MemoryEventSink()
    check = make_check(failed=failed, healed=healed, events=events)

    check.evaluate()

    assert "healing" in events.names()
    assert "healed" in events.names()
