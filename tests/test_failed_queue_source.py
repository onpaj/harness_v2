"""FailedQueueTaskSource — failed/ default-workflow failures -> healer tasks."""

from harness.drivers.failed_queue_source import FailedQueueTaskSource
from harness.drivers.memory import FakeClock, MemoryEventSink, MemoryTaskQueue
from harness.models import FAILED, HistoryEntry, Task
from harness.source_poller import SourcePoller


def _failed_task(task_id="tsk_1", workflow="default", reason="behavior raised an exception: boom", step="development", history_len=None):
    history = [
        HistoryEntry(at="t", actor="consumer:plan", from_step="plan", to_step=None, outcome="done"),
        HistoryEntry(
            at="t",
            actor=f"consumer:{step}",
            from_step=step,
            to_step=FAILED,
            reason=reason,
        ),
    ]
    if history_len is not None:
        # pad/extend to a specific length, keeping the FAILED entry last
        while len(history) < history_len:
            history.insert(-1, HistoryEntry(at="t", actor="operator", from_step=FAILED, to_step=None, reason="restarted by operator"))
    return Task(
        id=task_id,
        workflow_template=workflow,
        created="2026-07-22T10:00:00Z",
        repository="target-repo",
        status=FAILED,
        history=tuple(history),
    )


def build_source(failed, **kwargs):
    return FailedQueueTaskSource(
        failed=failed, clock=FakeClock(), healer_repo="harness_v2", **kwargs
    )


def test_default_workflow_failure_yields_one_healer_task():
    failed = MemoryTaskQueue("failed")
    failed.put(_failed_task())
    source = build_source(failed)

    [task] = source.poll()

    assert task.workflow_template == "healer"
    assert task.repository == "harness_v2"
    assert task.dedup_key == "failed-queue:tsk_1:2"
    assert task.data["failed_task"] == {
        "id": "tsk_1",
        "workflow": "default",
        "step": "development",
        "reason": "behavior raised an exception: boom",
        "repository": "target-repo",
    }
    assert "tsk_1" in task.data["request"]
    assert "development" in task.data["request"]
    assert task.data["source"] == {"kind": "failed-queue", "task": "tsk_1"}


def test_non_target_workflow_failure_yields_nothing():
    failed = MemoryTaskQueue("failed")
    failed.put(_failed_task(workflow="healer"))
    source = build_source(failed)

    assert source.poll() == []


def test_target_workflow_is_configurable():
    failed = MemoryTaskQueue("failed")
    failed.put(_failed_task(workflow="custom"))
    source = build_source(failed, target_workflow="custom")

    tasks = source.poll()

    assert len(tasks) == 1
    assert tasks[0].data["failed_task"]["workflow"] == "custom"


def test_task_not_yet_failed_is_ignored():
    failed = MemoryTaskQueue("failed")
    # nothing in failed/ yet
    source = build_source(failed)

    assert source.poll() == []


def test_repolling_unchanged_failed_queue_yields_no_second_task_via_poller():
    failed = MemoryTaskQueue("failed")
    failed.put(_failed_task())
    source = build_source(failed)
    inbox = MemoryTaskQueue("tasks")
    poller = SourcePoller(source=source, inbox=inbox, events=MemoryEventSink())

    first = poller.tick()
    second = poller.tick()

    assert first is True
    assert second is False
    assert len(inbox.list()) == 1


def test_restart_then_fail_again_yields_a_second_distinct_task():
    failed = MemoryTaskQueue("failed")
    original = _failed_task()
    failed.put(original)
    source = build_source(failed)
    inbox = MemoryTaskQueue("tasks")
    poller = SourcePoller(source=source, inbox=inbox, events=MemoryEventSink())

    assert poller.tick() is True
    assert len(inbox.list()) == 1

    # Simulate: operator restarted the task (still same id), then it failed
    # again later — history grew, so a *second* healer task must be created.
    failed._ready.clear()
    second_failure = _failed_task(history_len=4)
    failed.put(second_failure)

    assert poller.tick() is True
    healer_tasks = inbox.list()
    assert len(healer_tasks) == 2
    assert healer_tasks[0].dedup_key != healer_tasks[1].dedup_key


def test_no_failed_history_entry_yields_none_step_and_reason():
    failed = MemoryTaskQueue("failed")
    bare = Task(
        id="tsk_bare",
        workflow_template="default",
        created="2026-07-22T10:00:00Z",
        status=FAILED,
        history=(),
    )
    failed.put(bare)
    source = build_source(failed)

    [task] = source.poll()

    assert task.data["failed_task"]["step"] is None
    assert task.data["failed_task"]["reason"] is None


def test_report_progress_and_finish_are_noops():
    failed = MemoryTaskQueue("failed")
    source = build_source(failed)
    task = _failed_task()

    # must not raise
    source.report_progress(task, progress=None)
    source.finish(task, result=None)
