"""MergeReconciler — the core that archives done tasks once their PR merges."""

from harness.drivers.memory import FakeClock, FakeMergeChecker, MemoryEventSink, MemoryTaskQueue
from harness.merge_reconciler import MergeReconciler
from harness.models import Task


def _pr_task(task_id, *, repo="o/r", number=1, checked_at=None, url="https://github.com/o/r/pull/1"):
    pr = {"repo": repo, "number": number, "url": url, "branch": f"harness/{task_id}"}
    if checked_at is not None:
        pr["checkedAt"] = checked_at
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        status="end",
        data={"pr": pr},
    )


def _bare_task(task_id):
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        status="end",
        data={},
    )


def build():
    done = MemoryTaskQueue("done")
    archived = MemoryTaskQueue("archived")
    checker = FakeMergeChecker()
    events = MemoryEventSink()
    reconciler = MergeReconciler(
        done=done, archived=archived, checker=checker, events=events, clock=FakeClock()
    )
    return reconciler, done, archived, checker, events


def test_tick_on_empty_done_does_nothing():
    reconciler, *_ = build()

    assert reconciler.tick() is False


def test_task_without_pr_is_never_touched():
    reconciler, done, archived, _, events = build()
    done.put(_bare_task("tsk_1"))

    assert reconciler.tick() is False
    assert [t.id for t in done.list()] == ["tsk_1"]
    assert archived.list() == []
    assert events.events == []


def test_merged_pr_archives_the_task_and_emits_archived():
    reconciler, done, archived, checker, events = build()
    task = _pr_task("tsk_1")
    done.put(task)
    checker.merged.add(("o/r", 1))

    assert reconciler.tick() is True

    assert done.list() == []
    archived_tasks = archived.list()
    assert len(archived_tasks) == 1
    assert archived_tasks[0].id == "tsk_1"
    assert archived_tasks[0].history[-1].to_step == "archived"
    assert archived_tasks[0].history[-1].actor == "merge_reconciler"

    _, fields = next(e for e in events.events if e[0] == "archived")
    assert fields["task_id"] == "tsk_1"
    assert fields["queue"] == "archived"


def test_open_pr_stays_in_done_and_gets_checked_at_stamped():
    reconciler, done, archived, checker, events = build()
    done.put(_pr_task("tsk_1"))

    assert reconciler.tick() is False

    remaining = done.list()
    assert len(remaining) == 1
    assert remaining[0].data["pr"]["checkedAt"] == "2026-07-19T10:00:00Z"
    assert archived.list() == []

    _, fields = next(e for e in events.events if e[0] == "rechecked")
    assert fields["task_id"] == "tsk_1"
    assert fields["queue"] == "done"


def test_checker_exception_does_not_stop_the_loop():
    reconciler, done, archived, checker, events = build()
    done.put(_pr_task("tsk_1"))
    checker.raises.add(("o/r", 1))

    assert reconciler.tick() is False

    remaining = done.list()
    assert len(remaining) == 1
    # An errored check does not stamp checkedAt — it gets priority to retry.
    assert "checkedAt" not in remaining[0].data["pr"]
    assert archived.list() == []

    error_events = [e for e in events.events if e[0] == "merge_check_error"]
    assert len(error_events) == 1
    assert error_events[0][1]["task_id"] == "tsk_1"


def test_selection_is_least_recently_checked_avoiding_starvation():
    reconciler, done, archived, checker, _ = build()
    # tsk_1 was already checked recently; tsk_2 has never been checked.
    done.put(_pr_task("tsk_1", number=1, checked_at="2026-07-19T09:00:00Z"))
    done.put(_pr_task("tsk_2", number=2, checked_at=None))
    # Neither is merged — both remain open in this test.

    reconciler.tick()

    remaining = {t.id: t for t in done.list()}
    # tsk_2 (unset checkedAt sorts first) was examined, tsk_1 untouched.
    assert remaining["tsk_2"].data["pr"]["checkedAt"] == "2026-07-19T10:00:00Z"
    assert remaining["tsk_1"].data["pr"]["checkedAt"] == "2026-07-19T09:00:00Z"


def test_round_robin_avoids_starving_a_second_candidate():
    reconciler, done, archived, checker, _ = build()
    done.put(_pr_task("tsk_1", number=1))
    done.put(_pr_task("tsk_2", number=2))

    first_tick_examined = set()
    reconciler.tick()
    for t in done.list():
        if "checkedAt" in t.data["pr"]:
            first_tick_examined.add(t.id)

    reconciler.tick()
    second_tick_examined = set()
    for t in done.list():
        if "checkedAt" in t.data["pr"]:
            second_tick_examined.add(t.id)

    # Both candidates get examined across two ticks, not the same one twice.
    assert second_tick_examined == {"tsk_1", "tsk_2"}


def test_crash_between_claim_and_transfer_recovers_into_done():
    """No bespoke recovery code: `done.claim()` leaves the task claimed if the
    process dies before `transfer()` runs; `done.recover()` (the same queue
    machinery every other queue already has) puts it straight back."""
    reconciler, done, archived, checker, _ = build()
    task = _pr_task("tsk_1")
    done.put(task)
    checker.merged.add(("o/r", 1))

    done.claim(task, "lck_stale")  # simulates the reconciler's claim, then a crash

    assert done.list() == []  # claimed, not visible to list()

    recovered = done.recover()

    assert recovered == 1
    assert [t.id for t in done.list()] == ["tsk_1"]
    assert archived.list() == []


def test_task_carrying_malformed_pr_data_is_ignored():
    reconciler, done, archived, checker, events = build()
    task = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        status="end",
        data={"pr": "not-a-dict"},
    )
    done.put(task)

    assert reconciler.tick() is False
    assert [t.id for t in done.list()] == ["tsk_1"]
