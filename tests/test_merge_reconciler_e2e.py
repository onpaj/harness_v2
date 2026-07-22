"""End-to-end: land a task, mark its PR merged, tick the reconciler, watch it
leave `done`/the board while staying fetchable by id — on in-memory drivers.
"""

import json

from harness.app import Harness, HarnessLayout, build
from harness.drivers.memory import (
    FakeClock,
    FakeMergeChecker,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryWorkspace,
)
from harness.models import Task
from harness.ports.board import DONE_COLUMN

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
    ],
}

MAX_STEPS = 1000


def seed(tmp_path) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


async def drive_until_quiet(harness: Harness) -> int:
    for step in range(MAX_STEPS):
        acted = False
        if harness.dispatcher.tick():
            acted = True
        for consumer in harness.consumers:
            if await consumer.tick():
                acted = True
        if harness.reconciler is not None and harness.reconciler.tick():
            acted = True
        if not acted:
            return step
    raise AssertionError("loop did not settle")


def build_harness(tmp_path, checker):
    seed(tmp_path)
    return build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(),
        workspace=MemoryWorkspace(),
        artifacts=MemoryArtifactStore(),
        forge=MemoryForge(),
        merge_checker=checker,
        delay=0.0,
    )


async def test_landed_task_is_archived_once_its_pr_is_merged(tmp_path):
    checker = FakeMergeChecker()
    harness = build_harness(tmp_path, checker)
    task = Task(id="tsk_1", workflow_template="default", created="2026-07-21T10:00:00Z")
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    await drive_until_quiet(harness)

    # Landed: the task made it to `done` with a structured PR identity.
    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    landed = Task.from_dict(json.loads(done_files[0].read_text()))
    repo, number = landed.data["pr"]["repo"], landed.data["pr"]["number"]
    assert harness.projection.snapshot().workflow("default").column(DONE_COLUMN).tasks[0].id == "tsk_1"

    # The merge check has nothing to report yet — PR still open.
    await drive_until_quiet(harness)
    assert (tmp_path / "done" / "tsk_1.json").exists()
    assert harness.projection.snapshot().workflow("default").column(DONE_COLUMN).tasks[0].id == "tsk_1"

    # Merge on GitHub (the fake), then reconcile.
    checker.merged.add((repo, number))
    await drive_until_quiet(harness)

    assert not (tmp_path / "done" / "tsk_1.json").exists()
    archived_files = list((tmp_path / "archived").glob("*.json"))
    assert len(archived_files) == 1
    archived = Task.from_dict(json.loads(archived_files[0].read_text()))
    assert archived.id == "tsk_1"
    assert archived.history[-1].to_step == "archived"

    # Off the board, but still fetchable by id with full history and data.pr.
    board = harness.projection.snapshot()
    assert all(
        task.id != "tsk_1"
        for tab in board.workflows
        for column in tab.columns
        for task in column.tasks
    )
    fetched = harness.projection.get("tsk_1")
    assert fetched is not None
    assert fetched.data["pr"]["repo"] == repo
    assert any(entry.to_step == "archived" for entry in fetched.history)


async def test_task_without_a_merge_checker_stays_in_done_forever(tmp_path):
    """Backward compatibility: merge_checker=None → no reconciler, done tasks
    are never touched, exactly as before this feature."""
    harness = build_harness(tmp_path, checker=None)
    assert harness.reconciler is None
    task = Task(id="tsk_1", workflow_template="default", created="2026-07-21T10:00:00Z")
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    await drive_until_quiet(harness)

    assert (tmp_path / "done" / "tsk_1.json").exists()
    assert not (tmp_path / "archived").exists()
