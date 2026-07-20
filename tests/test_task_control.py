"""TaskControlService — the operator reset behind the TaskControl port."""

from harness.drivers.memory import FakeClock, MemoryEventSink, MemoryTaskQueue
from harness.models import FAILED, Task
from harness.task_control import TaskControlService


def make_failed_task(task_id="tsk_1", **kwargs):
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status=FAILED,
        last_outcome="request_changes",
        **kwargs,
    )


def build(failed_tasks=()):
    inbox = MemoryTaskQueue("tasks")
    failed = MemoryTaskQueue("failed")
    for task in failed_tasks:
        failed.put(task)
    events = MemoryEventSink()
    service = TaskControlService(
        inbox=inbox, failed=failed, events=events, clock=FakeClock()
    )
    return service, inbox, failed, events


def test_restart_moves_failed_task_into_inbox_with_state_reset():
    service, inbox, failed, _ = build([make_failed_task()])

    assert service.restart("tsk_1") is True

    assert failed.list() == []
    moved = inbox.list()
    assert len(moved) == 1
    assert moved[0].id == "tsk_1"
    assert moved[0].status is None
    assert moved[0].last_outcome is None
    assert moved[0].lock_id is None


def test_restart_appends_an_operator_history_entry():
    service, inbox, _, _ = build([make_failed_task()])

    service.restart("tsk_1")

    entry = inbox.list()[0].history[-1]
    assert entry.actor == "operator"
    assert entry.from_step == FAILED
    assert entry.reason == "restarted by operator"


def test_restart_emits_restarted_event_in_the_todo_column():
    service, _, _, events = build([make_failed_task()])

    service.restart("tsk_1")

    restarted = [(n, f) for n, f in events.events if n == "restarted"]
    assert len(restarted) == 1
    _, fields = restarted[0]
    assert fields["task_id"] == "tsk_1"
    assert fields["queue"] == "todo"
    assert fields["task"]["id"] == "tsk_1"
    assert fields["task"]["status"] is None


def test_restart_preserves_repository_worktree_and_data():
    task = make_failed_task(
        repository="acme", worktree="/wt/tsk_1", data={"title": "demo"}
    )
    service, inbox, _, _ = build([task])

    service.restart("tsk_1")

    moved = inbox.list()[0]
    assert moved.repository == "acme"
    assert moved.worktree == "/wt/tsk_1"
    assert moved.data == {"title": "demo"}


def test_restart_unknown_id_returns_false_and_does_nothing():
    service, inbox, _, events = build([make_failed_task()])

    assert service.restart("nope") is False
    assert inbox.list() == []
    assert [n for n, _ in events.events] == []
