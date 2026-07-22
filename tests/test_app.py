import asyncio
import json

import pytest

from harness.app import HarnessLayout, StepCollisionError, build
from harness.drivers.failed_queue_source import FailedQueueTaskSource
from harness.drivers.memory import MemoryEventSink
from harness.models import Task
from harness.ports.source import TaskSource


class ReplaySource(TaskSource):
    """Returns a task for the same source identity on every poll — the shape of
    an issue that keeps reappearing under the select label after a restart."""

    kind = "github"

    def __init__(self, issue):
        self._issue = issue
        self._n = 0

    def poll(self):
        self._n += 1
        return [
            Task(
                id=f"tsk_replay_{self._n}",
                workflow_template="default",
                created="2026-07-20T10:00:00Z",
                dedup_key=f"github:o/r:{self._issue}",
                data={"source": {"kind": "github", "repo": "o/r", "issue": self._issue}},
            )
        ]

    def report_progress(self, task, progress):  # pragma: no cover - not called
        pass

    def finish(self, task, result):  # pragma: no cover - not called
        pass

# `await runner` waits on the `asyncio.gather` inside Harness.run, which finishes
# only when both loops see `stop.is_set()`. If a regression broke that, `await
# runner` would hang forever and the test would freeze instead of failing.
# `asyncio.wait_for` turns that into a fast, clear failure.
RUNNER_TIMEOUT = 5.0

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "plan"},
    ],
}


HEALER_DEFINITION = {
    "name": "healer",
    "start": "diagnose",
    "transitions": [
        {"from": "diagnose", "on": "bug_confirmed", "to": "file_issue"},
        {"from": "diagnose", "on": "not_a_bug", "to": "end"},
        {"from": "file_issue", "on": "done", "to": "end"},
    ],
}


def seed(tmp_path):
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))
    return layout


def seed_healer(tmp_path):
    layout = seed(tmp_path)
    (layout.workflows / "healer.json").write_text(json.dumps(HEALER_DEFINITION))
    return layout


def test_build_creates_one_queue_per_step(tmp_path):
    seed(tmp_path)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert sorted(step for step in harness.workflow.steps()) == ["plan", "review"]
    assert (tmp_path / "queues" / "plan").is_dir()
    assert (tmp_path / "queues" / "review").is_dir()
    assert not (tmp_path / "queues" / "end").exists()
    assert len(harness.consumers) == 2


def test_build_creates_inbox_done_and_failed(tmp_path):
    seed(tmp_path)

    build(tmp_path, "default", events=MemoryEventSink())

    assert (tmp_path / "tasks").is_dir()
    assert (tmp_path / "done").is_dir()
    assert (tmp_path / "failed").is_dir()


def test_build_without_sources_has_no_pollers(tmp_path):
    """Backward compatibility: default sources=[] → no poller, behavior as before."""
    seed(tmp_path)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert harness.pollers == []


async def test_run_drives_a_task_all_the_way_to_done(tmp_path):
    seed(tmp_path)
    events = MemoryEventSink()
    harness = build(
        tmp_path,
        "default",
        events=events,
        delay=0.0,
        request_changes_once_at="review",
    )
    task = Task(id="tsk_1", workflow_template="default", created="2026-07-19T10:00:00Z")
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(400):
        await asyncio.sleep(0.01)
        if (tmp_path / "done" / "tsk_1.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    assert (tmp_path / "done" / "tsk_1.json").exists()
    finished = Task.from_dict(json.loads((tmp_path / "done" / "tsk_1.json").read_text()))
    visited = [entry.to_step for entry in finished.history if entry.actor == "dispatcher"]
    assert visited == ["plan", "review", "plan", "review", "end"]


def test_recover_returns_stranded_tasks(tmp_path):
    seed(tmp_path)
    harness = build(tmp_path, "default", events=MemoryEventSink())
    stranded = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        lock_id="lck_1",
    )
    (tmp_path / "tasks" / ".processing" / "tsk_1.json").write_text(
        json.dumps(stranded.to_dict())
    )

    assert harness.recover() == 1
    assert (tmp_path / "tasks" / "tsk_1.json").exists()
    revived = Task.from_dict(json.loads((tmp_path / "tasks" / "tsk_1.json").read_text()))
    assert revived.lock_id is None


async def test_projection_is_hydrated_from_queues_at_start(tmp_path):
    seed(tmp_path)
    harness = build(tmp_path, "default", delay=0.0)
    waiting = Task(
        id="tsk_9",
        workflow_template="default",
        created="2026-07-19T09:00:00Z",
        status="review",
    )
    (tmp_path / "queues" / "review" / "tsk_9.json").write_text(
        json.dumps(waiting.to_dict())
    )

    stop = asyncio.Event()
    stop.set()
    await harness.run(poll_interval=0.01, stop=stop)

    assert harness.projection.get("tsk_9") is not None
    assert harness.projection.snapshot().column("review").tasks[0].id == "tsk_9"


def test_seed_pollers_collects_from_every_queue(tmp_path):
    # A task for issue #1 already sits in `done/` (it ran to completion before a
    # restart). Seeding must find it there so the source can't re-ingest #1.
    seed(tmp_path)
    source = ReplaySource(issue=1)
    harness = build(tmp_path, "default", events=MemoryEventSink(), sources=[source])
    done_task = Task(
        id="tsk_done",
        workflow_template="default",
        created="2026-07-20T09:00:00Z",
        dedup_key="github:o/r:1",
        data={"source": {"kind": "github", "repo": "o/r", "issue": 1}},
    )
    (tmp_path / "done" / "tsk_done.json").write_text(json.dumps(done_task.to_dict()))

    harness._seed_pollers()

    # The source would hand back issue #1, but it is already on disk → skipped.
    assert harness.pollers[0].tick() is False
    assert list((tmp_path / "tasks").glob("tsk_*.json")) == []


async def test_stranded_task_survives_into_the_projection(tmp_path):
    seed(tmp_path)
    harness = build(tmp_path, "default", delay=0.0)
    stranded = Task(
        id="tsk_8",
        workflow_template="default",
        created="2026-07-19T09:00:00Z",
        status="plan",
        lock_id="lck_1",
    )
    (tmp_path / "queues" / "plan" / ".processing" / "tsk_8.json").write_text(
        json.dumps(stranded.to_dict())
    )

    stop = asyncio.Event()
    stop.set()
    await harness.run(poll_interval=0.01, stop=stop)

    assert harness.projection.get("tsk_8") is not None


# --- healing (heal=True) -----------------------------------------------------


def test_heal_without_healer_repo_raises(tmp_path):
    seed_healer(tmp_path)

    with pytest.raises(ValueError, match="--healer-repo"):
        build(tmp_path, "default", events=MemoryEventSink(), heal=True)


def test_heal_unions_step_queues_and_columns(tmp_path):
    seed_healer(tmp_path)

    harness = build(
        tmp_path, "default", events=MemoryEventSink(), heal=True, healer_repo="harness_v2"
    )

    assert sorted(harness._step_queues) == [
        "diagnose",
        "file_issue",
        "plan",
        "review",
    ]
    columns = [column.name for column in harness.projection.snapshot().columns]
    assert "diagnose" in columns
    assert "file_issue" in columns


def test_heal_registers_a_failed_queue_source(tmp_path):
    seed_healer(tmp_path)

    harness = build(
        tmp_path, "default", events=MemoryEventSink(), heal=True, healer_repo="harness_v2"
    )

    kinds = [poller._source.kind for poller in harness.pollers]
    assert "failed-queue" in kinds
    failed_source = next(
        poller._source for poller in harness.pollers if poller._source.kind == "failed-queue"
    )
    assert isinstance(failed_source, FailedQueueTaskSource)


def test_heal_false_builds_byte_identical_to_today(tmp_path):
    seed_healer(tmp_path)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert sorted(harness._step_queues) == ["plan", "review"]
    assert harness.pollers == []


def test_heal_with_colliding_step_name_raises(tmp_path):
    layout = seed(tmp_path)
    colliding = {
        "name": "healer",
        "start": "review",
        "transitions": [{"from": "review", "on": "bug_confirmed", "to": "end"}],
    }
    (layout.workflows / "healer.json").write_text(json.dumps(colliding))

    with pytest.raises(StepCollisionError):
        build(
            tmp_path, "default", events=MemoryEventSink(), heal=True, healer_repo="harness_v2"
        )
