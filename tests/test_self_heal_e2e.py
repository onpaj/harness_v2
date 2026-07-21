"""End-to-end self-healing on in-memory drivers.

A task fails (its workflow has no edge for the agent's outcome) → it lands in
`failed/` → the healer claims it, runs the `healer` persona, files an issue on
the harness repo, and settles the task onto `healed/`. No disk except the queues,
no real waiting.
"""

import json

from harness.app import Harness, HarnessLayout, HealConfig, build
from harness.drivers.memory import (
    FakeAgentRunner,
    FakeClock,
    MemoryAgentCatalog,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryIssueTracker,
    MemoryWorkspace,
)
from harness.models import FAILED, HEALED, Outcome, Task
from harness.ports.agent import AgentRun, AgentSpec

# `work` has only a request_changes edge, so the agent's `done` cannot be routed
# → the task fails. That is the induced failure the healer then picks up.
DEFINITION = {
    "name": "default",
    "start": "work",
    "transitions": [
        {"from": "work", "on": "request_changes", "to": "end"},
    ],
}

MAX_STEPS = 1000

WORK_SPEC = AgentSpec(name="work", prompt="p", allowed_outcomes=(Outcome.DONE,))
HEALER_SPEC = AgentSpec(
    name="healer",
    prompt="persona",
    allowed_outcomes=(Outcome.DONE, Outcome.REQUEST_CHANGES),
)


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
        if harness.healer is not None and await harness.healer.tick():
            acted = True
        if not acted:
            return step
    raise AssertionError("loop did not settle")


def build_harness(tmp_path, runner) -> Harness:
    seed(tmp_path)
    return build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(),
        workspace=MemoryWorkspace(),
        artifacts=MemoryArtifactStore(),
        runner=runner,
        catalog=MemoryAgentCatalog({"work": WORK_SPEC, "healer": HEALER_SPEC}),
        issue_tracker=MemoryIssueTracker(),
        heal=HealConfig(repository="onpaj/harness_v2"),
        delay=0.0,
    )


def submit(tmp_path, task_id="tsk_e2e") -> None:
    task = Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-21T10:00:00Z",
        repository="app",
        data={"request": "Do the thing"},
    )
    (tmp_path / "tasks" / f"{task_id}.json").write_text(json.dumps(task.to_dict()))


async def test_failed_task_is_healed_into_an_issue(tmp_path):
    runner = FakeAgentRunner(
        runs={"healer": AgentRun(Outcome.DONE, "Add the missing edge")},
        writes={"healer": {"issue.md": "# Add the missing edge\n\nrouting gap"}},
        default=AgentRun(Outcome.DONE, "did the work"),
    )
    harness = build_harness(tmp_path, runner)
    submit(tmp_path)

    await drive_until_quiet(harness)

    # the task ended in healed/, not failed/
    assert list((tmp_path / "failed").glob("*.json")) == []
    healed_files = list((tmp_path / "healed").glob("*.json"))
    assert len(healed_files) == 1
    healed = Task.from_dict(json.loads(healed_files[0].read_text()))
    assert healed.status == HEALED
    assert "opened issue" in healed.history[-1].summary

    # exactly one issue on the harness repo, keyed by the failed task id
    tracker = harness.healer._tracker
    assert len(tracker.opened) == 1
    assert tracker.opened[0]["repo"] == "onpaj/harness_v2"
    assert tracker.opened[0]["marker"] == "tsk_e2e"


async def test_healer_finds_nothing_actionable(tmp_path):
    runner = FakeAgentRunner(
        runs={"healer": AgentRun(Outcome.REQUEST_CHANGES, "external flake")},
        default=AgentRun(Outcome.DONE, "did the work"),
    )
    harness = build_harness(tmp_path, runner)
    submit(tmp_path)

    await drive_until_quiet(harness)

    assert list((tmp_path / "failed").glob("*.json")) == []
    assert len(list((tmp_path / "healed").glob("*.json"))) == 1
    assert harness.healer._tracker.opened == []  # no issue filed


async def test_heal_time_error_does_not_loop_back_to_failed(tmp_path):
    class BoomRunner(FakeAgentRunner):
        async def run(self, *, prompt, spec, cwd, timeout, on_output=None):
            if spec.name == "healer":
                raise RuntimeError("claude timed out")
            return AgentRun(Outcome.DONE, "did the work")

    harness = build_harness(tmp_path, BoomRunner())
    submit(tmp_path)

    # settles despite the heal error — and drive_until_quiet returning proves it
    # did not loop (a task bouncing failed→failed would never go quiet).
    await drive_until_quiet(harness)

    assert list((tmp_path / "failed").glob("*.json")) == []
    healed_files = list((tmp_path / "healed").glob("*.json"))
    assert len(healed_files) == 1
    healed = Task.from_dict(json.loads(healed_files[0].read_text()))
    assert healed.status == HEALED
    assert "heal-failed" in healed.history[-1].summary


async def test_no_healer_leaves_the_task_in_failed(tmp_path):
    # Backward-compatible path: build without heal → failed/ is a dead end.
    seed(tmp_path)
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(),
        workspace=MemoryWorkspace(),
        artifacts=MemoryArtifactStore(),
        runner=FakeAgentRunner(default=AgentRun(Outcome.DONE, "did the work")),
        catalog=MemoryAgentCatalog({"work": WORK_SPEC}),
        delay=0.0,
    )
    submit(tmp_path)

    await drive_until_quiet(harness)

    assert harness.healer is None
    assert len(list((tmp_path / "failed").glob("*.json"))) == 1
    assert not (tmp_path / "healed").exists() or list(
        (tmp_path / "healed").glob("*.json")
    ) == []
