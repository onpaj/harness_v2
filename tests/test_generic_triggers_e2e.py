"""End-to-end for scheduled triggers on in-memory drivers.

A `ScheduledTrigger` is a `TaskSource` that rides the existing `sources` list:
it produces tasks on a clock gate and reflects nothing outward (a `Trigger`).
This mirrors `test_phase4_e2e.py` — `FakeClock`, in-memory workspace/artifacts/
forge — with no disk beyond the queues and no real waiting.
"""

import json

from harness.app import HarnessLayout, build
from harness.drivers.checks import AlwaysCheck
from harness.drivers.memory import (
    FakeClock,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryWorkspace,
    ScriptedBehavior,
)
from harness.drivers.scheduled_trigger import ScheduledTrigger
from harness.models import MoveTo, Task
from harness.router import route

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "development"},
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


async def drive_until_quiet(harness) -> int:
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


def build_harness(tmp_path, sources, *, clock, behavior=None):
    seed(tmp_path)
    return build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=clock,
        behavior=behavior,
        workspace=MemoryWorkspace(),
        artifacts=MemoryArtifactStore(),
        forge=MemoryForge(),
        delay=0.0,
        sources=sources or None,
    )


async def test_trigger_fires_a_task_that_reaches_done_and_reflects_nothing(tmp_path):
    clock = FakeClock()
    trigger = ScheduledTrigger(
        name="tick",
        clock=clock,
        interval=3600,
        check=AlwaysCheck(),
        workflow="default",
    )
    harness = build_harness(tmp_path, [trigger], clock=clock)

    await drive_until_quiet(harness)

    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    finished = Task.from_dict(json.loads(done_files[0].read_text()))
    assert finished.status == "end"
    # A trigger stamps no `data.source`, so the reflector ignores it — nothing
    # was projected outward. (A `Trigger`'s report_progress/finish are no-ops.)
    assert "source" not in finished.data


async def test_trigger_fires_a_second_task_after_the_clock_crosses_a_boundary(
    tmp_path,
):
    clock = FakeClock()
    trigger = ScheduledTrigger(
        name="tick",
        clock=clock,
        interval=3600,
        check=AlwaysCheck(),
        workflow="default",
    )
    harness = build_harness(tmp_path, [trigger], clock=clock)

    await drive_until_quiet(harness)
    first = list((tmp_path / "done").glob("*.json"))
    assert len(first) == 1
    first_id = Task.from_dict(json.loads(first[0].read_text())).id

    # Same interval bucket → the gate returns [] and no new task is ingested.
    await drive_until_quiet(harness)
    assert len(list((tmp_path / "done").glob("*.json"))) == 1

    # Advance the clock one full interval → a new bucket → a second, distinct task.
    clock.instant = "2026-07-19T11:00:00Z"
    await drive_until_quiet(harness)

    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 2
    ids = {Task.from_dict(json.loads(p.read_text())).id for p in done_files}
    assert len(ids) == 2
    assert first_id in ids


async def test_step_target_task_is_placed_by_the_dispatcher(tmp_path):
    """A workflow-less `step` target: the trigger emits, the dispatcher places.

    Nobody writes into the step queue directly — the trigger puts the task in
    the inbox and the dispatcher routes it into `development` (route() returns
    MoveTo(step) for a workflow-less task). The `development` consumer then
    picks it up, proving placement.
    """
    clock = FakeClock()
    trigger = ScheduledTrigger(
        name="cleanup",
        clock=clock,
        interval=3600,
        check=AlwaysCheck(),
        step="development",
    )
    behavior = ScriptedBehavior()
    harness = build_harness(tmp_path, [trigger], clock=clock, behavior=behavior)

    await drive_until_quiet(harness)

    # The development consumer saw the task — it was placed there by the
    # dispatcher, not written to the queue by hand.
    assert "development" in behavior.seen

    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    finished = Task.from_dict(json.loads(done_files[0].read_text()))
    assert finished.status == "end"


def test_route_places_a_workflow_less_step_task(tmp_path):
    """Unit-level twin of the placement path: a fresh workflow-less step task
    routes to MoveTo(step), with no workflow template attached."""
    clock = FakeClock()
    trigger = ScheduledTrigger(
        name="cleanup",
        clock=clock,
        interval=3600,
        check=AlwaysCheck(),
        step="development",
    )
    tasks = trigger.poll()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.step == "development"
    assert task.workflow_template is None
    assert route(task, None) == MoveTo("development")


async def test_no_sources_behaves_as_before(tmp_path):
    """Backward-compat: with no trigger (sources=None) the harness runs exactly
    as today — no pollers, and a submitted task still flows to done."""
    clock = FakeClock()
    harness = build_harness(tmp_path, None, clock=clock)
    assert harness.pollers == []

    task = Task(
        id="tsk_plain",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        data={"title": "manual task"},
    )
    (tmp_path / "tasks" / "tsk_plain.json").write_text(json.dumps(task.to_dict()))

    await drive_until_quiet(harness)

    finished = Task.from_dict(
        json.loads((tmp_path / "done" / "tsk_plain.json").read_text())
    )
    assert finished.status == "end"
