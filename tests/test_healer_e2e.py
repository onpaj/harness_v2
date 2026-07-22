"""End-to-end: a default-workflow failure is healed automatically.

failed/ -> FailedQueueTaskSource -> healer task -> diagnose -> (file_issue |
end). All in-memory, no disk beyond the queues and no real waiting — the same
shape as test_phase4_e2e.py.
"""

import json

from harness.app import Harness, HarnessLayout, build
from harness.drivers.memory import (
    FakeClock,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryWorkspace,
)
from harness.models import BehaviorResult, Outcome, Task
from harness.ports.behavior import ConsumerBehavior

DEFAULT_DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "end"},
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

MAX_STEPS = 1000


class ScriptedWithFailure(ConsumerBehavior):
    """Like `ScriptedBehavior`, but the given step always raises — the shape
    of a real harness bug blowing up mid-step."""

    def __init__(self, outcomes=None, fail_step=None):
        self._outcomes = {step: list(values) for step, values in (outcomes or {}).items()}
        self._fail_step = fail_step

    async def run(self, task: Task) -> BehaviorResult:
        step = task.status or ""
        if step == self._fail_step:
            raise RuntimeError("boom: development step exploded")
        pending = self._outcomes.get(step)
        outcome = pending.pop(0) if pending else Outcome.DONE
        return BehaviorResult(outcome, summary=f"{step}: {outcome.value}")


def seed(tmp_path) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFAULT_DEFINITION))
    (layout.workflows / "healer.json").write_text(json.dumps(HEALER_DEFINITION))


async def drive_until_quiet(harness: Harness) -> int:
    for step in range(MAX_STEPS):
        acted = False
        for poller in harness.pollers:
            if poller.tick():
                acted = True
        if harness.dispatcher.tick():
            acted = True
        for consumer in harness.consumers:
            if await consumer.tick():
                acted = True
        if not acted:
            return step
    raise AssertionError("loop did not settle")


def build_harness(tmp_path, *, diagnose_outcome: Outcome, forge=None):
    seed(tmp_path)
    behavior = ScriptedWithFailure(
        outcomes={"diagnose": [diagnose_outcome]}, fail_step="development"
    )
    return build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(),
        workspace=MemoryWorkspace(),
        artifacts=MemoryArtifactStore(),
        forge=forge or MemoryForge(),
        behavior=behavior,
        delay=0.0,
        heal=True,
        healer_repo="harness_v2",
    )


async def test_confirmed_bug_files_an_issue_and_reaches_done(tmp_path):
    forge = MemoryForge()
    harness = build_harness(tmp_path, diagnose_outcome=Outcome.BUG_CONFIRMED, forge=forge)
    task = Task(id="tsk_1", workflow_template="default", created="2026-07-22T10:00:00Z")
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    await drive_until_quiet(harness)

    # the original task landed in failed/, untouched by healing
    failed_files = list((tmp_path / "failed").glob("*.json"))
    assert len(failed_files) == 1
    failed = Task.from_dict(json.loads(failed_files[0].read_text()))
    assert failed.id == "tsk_1"
    assert failed.status == "failed"

    # exactly one healer task ran to done, and one issue was filed
    done_files = list((tmp_path / "done").glob("*.json"))
    healer_tasks = [
        Task.from_dict(json.loads(f.read_text()))
        for f in done_files
        if json.loads(f.read_text())["workflowTemplate"] == "healer"
    ]
    assert len(healer_tasks) == 1
    assert healer_tasks[0].data["failed_task"]["id"] == "tsk_1"

    assert len(forge.issues) == 1


async def test_not_a_bug_reaches_done_with_no_issue_filed(tmp_path):
    forge = MemoryForge()
    harness = build_harness(tmp_path, diagnose_outcome=Outcome.NOT_A_BUG, forge=forge)
    task = Task(id="tsk_1", workflow_template="default", created="2026-07-22T10:00:00Z")
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    await drive_until_quiet(harness)

    done_files = list((tmp_path / "done").glob("*.json"))
    healer_tasks = [
        Task.from_dict(json.loads(f.read_text()))
        for f in done_files
        if json.loads(f.read_text())["workflowTemplate"] == "healer"
    ]
    assert len(healer_tasks) == 1
    assert healer_tasks[0].last_outcome == "not_a_bug"

    assert forge.issues == []


async def test_repolling_the_same_failure_does_not_double_heal(tmp_path):
    forge = MemoryForge()
    harness = build_harness(tmp_path, diagnose_outcome=Outcome.BUG_CONFIRMED, forge=forge)
    task = Task(id="tsk_1", workflow_template="default", created="2026-07-22T10:00:00Z")
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    await drive_until_quiet(harness)
    # poll again after everything has already settled — nothing new should happen
    await drive_until_quiet(harness)

    done_files = list((tmp_path / "done").glob("*.json"))
    healer_tasks = [
        f for f in done_files if json.loads(f.read_text())["workflowTemplate"] == "healer"
    ]
    assert len(healer_tasks) == 1
    assert len(forge.issues) == 1


async def test_healer_task_own_failure_does_not_get_healed_again(tmp_path):
    """The healer workflow's own failures are not targeted by
    FailedQueueTaskSource (target_workflow defaults to the primary workflow),
    which is what stops the healer from healing itself forever."""
    seed(tmp_path)
    behavior = ScriptedWithFailure(fail_step="development")

    # diagnose itself blows up this time.
    class AlwaysFailDiagnose(ConsumerBehavior):
        async def run(self, task: Task) -> BehaviorResult:
            step = task.status or ""
            if step == "diagnose":
                raise RuntimeError("diagnose blew up too")
            if step == "development":
                raise RuntimeError("boom: development step exploded")
            return BehaviorResult(Outcome.DONE, f"{step}: done")

    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(),
        workspace=MemoryWorkspace(),
        artifacts=MemoryArtifactStore(),
        forge=MemoryForge(),
        behavior=AlwaysFailDiagnose(),
        delay=0.0,
        heal=True,
        healer_repo="harness_v2",
    )
    task = Task(id="tsk_1", workflow_template="default", created="2026-07-22T10:00:00Z")
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    await drive_until_quiet(harness)

    failed_files = [
        json.loads(f.read_text()) for f in (tmp_path / "failed").glob("*.json")
    ]
    workflows_failed = {record["workflowTemplate"] for record in failed_files}
    # both the original task and the healer task itself end up in failed/ ...
    assert workflows_failed == {"default", "healer"}
    # ... but no THIRD (meta-healer) task was ever created for the healer's own failure.
    assert len(failed_files) == 2
