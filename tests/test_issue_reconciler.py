"""IssueReconciler — retires tasks whose source issue was closed or deleted."""

from harness.drivers.memory import (
    FakeClock,
    FakeIssueChecker,
    MemoryEventSink,
    MemoryTaskQueue,
)
from harness.issue_reconciler import IssueReconciler
from harness.models import ARCHIVED, Task


def _github_task(task_id="tsk_1", *, repo="o/r", issue=1, status="plan") -> Task:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-22T10:00:00Z",
        status=status,
        data={"source": {"kind": "github", "repo": repo, "issue": issue}},
    )


def _submitted_task(task_id="tsk_plain") -> Task:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-22T10:00:00Z",
        status="plan",
        data={"title": "hand-submitted"},
    )


def _build(*queues):
    archived = MemoryTaskQueue("archived")
    checker = FakeIssueChecker()
    events = MemoryEventSink()
    reconciler = IssueReconciler(
        queues=list(queues),
        archived=archived,
        checker=checker,
        events=events,
        clock=FakeClock(),
    )
    return reconciler, archived, checker, events


def test_open_issue_leaves_the_task_untouched():
    todo = MemoryTaskQueue("tasks")
    reconciler, archived, _, events = _build(todo)
    todo.put(_github_task())

    assert reconciler.tick() is False
    assert [t.id for t in todo.list()] == ["tsk_1"]
    assert archived.list() == []
    assert "archived" not in events.names()


def test_closed_issue_archives_the_task_off_the_board():
    todo = MemoryTaskQueue("tasks")
    reconciler, archived, checker, events = _build(todo)
    todo.put(_github_task())
    checker.closed.add(("o/r", 1))

    assert reconciler.tick() is True

    assert todo.list() == []
    archived_task = archived.list()[0]
    assert archived_task.id == "tsk_1"
    assert archived_task.status == ARCHIVED
    assert archived_task.lock_id is None

    entry = archived_task.history[-1]
    assert entry.actor == "issue_reconciler"
    assert entry.from_step == "plan"
    assert entry.to_step is None
    assert entry.reason == "source issue closed"

    _, fields = next(item for item in events.events if item[0] == "archived")
    assert fields["task_id"] == "tsk_1"
    assert fields["queue"] == "archived"


def test_deleted_issue_is_archived_too():
    # A `None` from the checker (issue gone) is "not open", same as closed.
    class GoneChecker(FakeIssueChecker):
        def is_open(self, task):
            return False  # simulate a 404 → gone

    todo = MemoryTaskQueue("tasks")
    archived = MemoryTaskQueue("archived")
    reconciler = IssueReconciler(
        queues=[todo],
        archived=archived,
        checker=GoneChecker(),
        events=MemoryEventSink(),
        clock=FakeClock(),
    )
    todo.put(_github_task())

    assert reconciler.tick() is True
    assert [t.id for t in archived.list()] == ["tsk_1"]


def test_submitted_task_without_source_is_never_touched():
    todo = MemoryTaskQueue("tasks")
    reconciler, archived, _, events = _build(todo)
    todo.put(_submitted_task())

    assert reconciler.tick() is False
    assert [t.id for t in todo.list()] == ["tsk_plain"]
    assert archived.list() == []
    assert events.events == []


def test_sweeps_every_queue_it_is_given():
    todo = MemoryTaskQueue("tasks")
    development = MemoryTaskQueue("development")
    done = MemoryTaskQueue("done")
    reconciler, archived, checker, _ = _build(todo, development, done)
    todo.put(_github_task("tsk_todo", issue=1, status=None))
    development.put(_github_task("tsk_dev", issue=2, status="development"))
    done.put(_github_task("tsk_done", issue=3, status="end"))
    # Only the mid-workflow and the done task's issues were closed.
    checker.closed.update({("o/r", 2), ("o/r", 3)})

    assert reconciler.tick() is True

    assert [t.id for t in todo.list()] == ["tsk_todo"]  # its issue is still open
    assert development.list() == []
    assert done.list() == []
    assert {t.id for t in archived.list()} == {"tsk_dev", "tsk_done"}


def test_checker_error_is_isolated_and_does_not_stop_the_tick():
    todo = MemoryTaskQueue("tasks")
    reconciler, archived, checker, events = _build(todo)
    todo.put(_github_task("tsk_bad", issue=1))
    todo.put(_github_task("tsk_ok", issue=2))
    checker.raises.add(("o/r", 1))
    checker.closed.add(("o/r", 2))

    assert reconciler.tick() is True

    # The good task was still archived despite the bad one raising.
    assert [t.id for t in todo.list()] == ["tsk_bad"]
    assert [t.id for t in archived.list()] == ["tsk_ok"]

    errors = [fields for name, fields in events.events if name == "issue_check_error"]
    assert len(errors) == 1
    assert errors[0]["task_id"] == "tsk_bad"
    assert "issue check failed" in errors[0]["error"]


def test_empty_queues_are_a_cheap_noop():
    reconciler, archived, _, _ = _build(MemoryTaskQueue("tasks"))

    assert reconciler.tick() is False
    assert archived.list() == []


class RaceQueue(MemoryTaskQueue):
    """Lists a task but always loses the claim race — another actor (a consumer
    or a concurrent housekeeping loop) claimed it between list() and claim()."""

    def claim(self, task, lock_id):
        return None


def test_lost_claim_race_does_not_count_as_archived():
    todo = RaceQueue("tasks")
    archived = MemoryTaskQueue("archived")
    checker = FakeIssueChecker()
    checker.closed.add(("o/r", 1))
    reconciler = IssueReconciler(
        queues=[todo],
        archived=archived,
        checker=checker,
        events=MemoryEventSink(),
        clock=FakeClock(),
    )
    todo.put(_github_task())

    assert reconciler.tick() is False
    assert archived.list() == []


def test_crash_between_claim_and_transfer_recovers_into_the_queue():
    """Same queue machinery as every other loop: a claim without a transfer is
    put straight back by recover(), no bespoke recovery code."""
    todo = MemoryTaskQueue("tasks")
    reconciler, archived, checker, _ = _build(todo)
    task = _github_task()
    todo.put(task)
    checker.closed.add(("o/r", 1))

    todo.claim(task, "lck_stale")  # simulates the claim, then a crash
    assert todo.list() == []

    assert todo.recover() == 1
    assert [t.id for t in todo.list()] == ["tsk_1"]
    assert archived.list() == []
