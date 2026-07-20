"""End-to-end fáze 2 na in-memory pracovních driverech.

Task proteče plan→…→review→land→end s jednou request_changes smyčkou. Ověřuje
se celý pipe: artefakty (attempt-indexed), per-fázové commity, PR na konci a
summary v historii. Bez disku (kromě front) a bez reálného čekání.
"""

import json

from harness.app import Harness, HarnessLayout, build
from harness.drivers.memory import (
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryWorkspace,
)
from harness.models import Task

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "design"},
        {"from": "design", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "development"},
    ],
}

MAX_STEPS = 1000


def seed(tmp_path) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


async def drive_until_quiet(harness: Harness) -> int:
    for step in range(MAX_STEPS):
        acted = harness.dispatcher.tick()
        for consumer in harness.consumers:
            if await consumer.tick():
                acted = True
        if not acted:
            return step
    raise AssertionError("smyčka se nezklidnila")


def submit(tmp_path, task: Task) -> None:
    (tmp_path / "tasks" / f"{task.id}.json").write_text(json.dumps(task.to_dict()))


async def build_and_run(tmp_path):
    seed(tmp_path)
    workspace = MemoryWorkspace()
    artifacts = MemoryArtifactStore()
    forge = MemoryForge()
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        workspace=workspace,
        artifacts=artifacts,
        forge=forge,
        delay=0.0,
        request_changes_once_at="review",
    )
    task = Task(
        id="tsk_e2e",
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        repository="app-backend",
        worktree="/work/tsk_e2e",
        data={"title": "přidat rate limiting"},
    )
    submit(tmp_path, task)
    await drive_until_quiet(harness)
    finished = Task.from_dict(
        json.loads((tmp_path / "done" / "tsk_e2e.json").read_text())
    )
    return finished, workspace, artifacts, forge


async def test_task_reaches_done_through_land(tmp_path):
    finished, *_ = await build_and_run(tmp_path)

    assert finished.status == "end"
    routed = [e.to_step for e in finished.history if e.actor == "dispatcher"]
    assert routed[-2:] == ["land", "end"]


async def test_request_changes_loop_creates_second_attempt(tmp_path):
    _, _, artifacts, _ = await build_and_run(tmp_path)

    # development i review běžely dvakrát → attempt 0 i 1
    assert artifacts.read("tsk_e2e", "development", 0, "development.md") is not None
    assert artifacts.read("tsk_e2e", "development", 1, "development.md") is not None
    assert artifacts.read("tsk_e2e", "review", 0, "review.md") is not None
    assert artifacts.read("tsk_e2e", "review", 1, "review.md") is not None


async def test_workspace_carries_per_phase_commits(tmp_path):
    _, workspace, _, _ = await build_and_run(tmp_path)

    messages = workspace.handles["tsk_e2e"].commits
    # každá fáze commitla se smysluplnou zprávou; landing přiklopil artefakty
    assert any(m.startswith("[plan]") for m in messages)
    assert any(m.startswith("[development]") for m in messages)
    assert "[land] artefakty tasku" in messages


async def test_landing_opens_exactly_one_pull_request(tmp_path):
    _, _, _, forge = await build_and_run(tmp_path)

    assert len(forge.opened) == 1
    pull = forge.opened[0]
    assert pull.branch == "harness/tsk_e2e"
    assert pull.title == "přidat rate limiting"
    assert "development" in forge.bodies["harness/tsk_e2e"]


async def test_history_carries_summaries(tmp_path):
    finished, *_ = await build_and_run(tmp_path)

    consumer_entries = [e for e in finished.history if e.actor.startswith("consumer:")]
    assert consumer_entries
    assert all(e.summary for e in consumer_entries)
