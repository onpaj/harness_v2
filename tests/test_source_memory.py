"""MemoryTaskSource — in-memory driver for the TaskSource port."""

from harness.drivers.memory import FakeClock, MemoryTaskSource
from harness.models import Task
from harness.ports.source import FinishResult, Progress


def test_two_submits_yield_two_tasks_with_source_kind():
    source = MemoryTaskSource(clock=FakeClock())
    source.submit("Fix bug")
    source.submit("Add feature")

    tasks = source.poll()

    assert len(tasks) == 2
    assert all(t.data["source"]["kind"] == "memory" for t in tasks)
    assert {t.data["title"] for t in tasks} == {"Fix bug", "Add feature"}


def test_second_poll_without_new_submit_is_empty():
    source = MemoryTaskSource(clock=FakeClock())
    source.submit("Fix bug")

    first = source.poll()
    second = source.poll()

    assert len(first) == 1
    assert second == []


def test_poll_builds_task_with_worktree_and_repository():
    source = MemoryTaskSource(
        clock=FakeClock(), repository="app-backend", worktree_root="/wt"
    )
    issue = source.submit("Fix bug", body="details")

    [task] = source.poll()

    assert task.repository == "app-backend"
    assert task.worktree == f"/wt/{task.id}"
    assert task.created == "2026-07-19T10:00:00Z"
    assert task.workflow_template == "default"
    assert task.data["body"] == "details"
    assert task.data["source"]["issue"] == issue


def test_report_progress_and_finish_record_projection_under_issue():
    source = MemoryTaskSource(clock=FakeClock())
    issue = source.submit("Fix bug")
    [task] = source.poll()

    source.report_progress(task, Progress(step="development"))
    source.finish(task, FinishResult(ok=True))

    assert source.states[issue] == [
        ("progress", "development"),
        ("finish", True),
    ]


def test_foreign_kind_task_is_ignored_by_guard():
    source = MemoryTaskSource(clock=FakeClock())
    foreign = Task(
        id="tsk_x",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        data={"source": {"kind": "github", "issue": 7}},
    )

    source.report_progress(foreign, Progress(step="development"))
    source.finish(foreign, FinishResult(ok=False))

    assert source.states == {}
