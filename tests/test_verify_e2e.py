"""E2E: a red verify run loops the task back to development, a green one
lets it through to land. In-memory drivers, DummyBehavior for agent steps,
scripted MemoryCommandRunner for the verify step."""

from __future__ import annotations

import json

from harness.app import HarnessLayout, build
from harness.drivers.memory import MemoryCommandRunner, MemoryRepositoryRegistry
from harness.models import Task
from harness.ports.command import CommandResult

DEFINITION = {
    "name": "default",
    "start": "development",
    "transitions": [
        {"from": "development", "on": "done", "to": "verify"},
        {"from": "verify", "on": "done", "to": "land"},
        {"from": "verify", "on": "request_changes", "to": "development"},
        {"from": "land", "on": "done", "to": "end"},
    ],
    "finishers": {"verify": "verify"},
}

MAX_STEPS = 200


def seed(tmp_path) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


def submit(tmp_path, task: Task) -> None:
    (tmp_path / "tasks" / f"{task.id}.json").write_text(json.dumps(task.to_dict()))


async def drive_until_quiet(harness) -> None:
    for _ in range(MAX_STEPS):
        acted = harness.dispatcher.tick()
        for consumer in harness.consumers:
            if await consumer.tick():
                acted = True
        if not acted:
            return
    raise AssertionError("loop did not settle")


async def test_red_verify_loops_back_then_green_lands(tmp_path):
    seed(tmp_path)
    runner = MemoryCommandRunner(
        results=[CommandResult(1, "1 failed"), CommandResult(0, "all passed")]
    )
    harness = build(
        tmp_path,
        "default",
        delay=0.0,
        command_runner=runner,
        repository_registry=MemoryRepositoryRegistry(
            {"app": tmp_path}, verify={"app": "make test"}
        ),
    )
    task = Task.from_dict(
        {
            "id": "tsk_verify_e2e",
            "workflowTemplate": "default",
            "repository": "app",
            "created": "2026-07-24T00:00:00Z",
            "data": {"title": "verify e2e"},
        }
    )
    submit(tmp_path, task)
    await drive_until_quiet(harness)

    assert (tmp_path / "done" / "tsk_verify_e2e.json").exists(), "task did not reach done/"
    finished = Task.from_dict(
        json.loads((tmp_path / "done" / "tsk_verify_e2e.json").read_text())
    )

    # verify ran twice: red then green.
    assert [call["command"] for call in runner.calls] == ["make test", "make test"]

    # History shows the loop: development consumed twice, verify twice with
    # request_changes then done.
    verify_outcomes = [
        entry.outcome
        for entry in finished.history
        if entry.actor == "consumer:verify"
    ]
    assert verify_outcomes == ["request_changes", "done"]
    development_runs = [
        entry
        for entry in finished.history
        if entry.actor == "consumer:development"
    ]
    assert len(development_runs) == 2
