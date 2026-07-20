from harness.drivers.memory import MemoryWorkspace
from harness.models import Task


def make_task(task_id="tsk_1") -> Task:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        repository="app-backend",
        worktree="/work/app-backend/tsk_1",
        status="development",
    )


def test_attach_derives_branch_from_task_id():
    handle = MemoryWorkspace().attach(make_task())

    assert handle.branch == "harness/tsk_1"


def test_reattach_returns_same_handle():
    workspace = MemoryWorkspace()
    task = make_task()

    first = workspace.attach(task)
    first.write("a.py", "x")
    second = workspace.attach(task)

    assert second is first
    assert second.writes == [("a.py", "x")]


def test_commit_records_message_and_returns_sha():
    handle = MemoryWorkspace().attach(make_task())

    sha = handle.commit("[development] done")

    assert sha is not None
    assert handle.commits == ["[development] done"]
