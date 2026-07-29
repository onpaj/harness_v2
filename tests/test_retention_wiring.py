"""`build()` always wires a RetentionReconciler over the terminal queues."""

import asyncio
import json

from harness.app import HarnessLayout, build
from harness.drivers.memory import FakeClock, MemoryEventSink
from harness.models import ARCHIVED, HistoryEntry, Task
from harness.retention_reconciler import DEFAULT_RETENTION_DAYS

NOW = "2026-07-28T12:00:00Z"

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "plan"},
    ],
}


def seed(tmp_path):
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


def _settled(task_id, *, at, status):
    return Task(
        id=task_id,
        workflow_template=None,
        created="2026-07-01T10:00:00Z",
        status=status,
        history=(
            HistoryEntry(at=at, actor="consumer", from_step="plan", to_step=None),
        ),
    )


def test_build_always_wires_a_retention_reconciler(tmp_path):
    seed(tmp_path)
    harness = build(tmp_path, "default", events=MemoryEventSink())
    assert harness.retention_reconciler is not None


def test_the_sweep_archives_old_terminal_tasks_from_all_three_queues(tmp_path):
    seed(tmp_path)
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(NOW),
        retention_days=2,
    )
    harness._done.put(_settled("tsk_d", at="2026-07-20T12:00:00Z", status="done"))
    harness._failed.put(_settled("tsk_f", at="2026-07-20T12:00:00Z", status="failed"))
    harness._healed.put(_settled("tsk_h", at="2026-07-20T12:00:00Z", status="healed"))

    assert harness.retention_reconciler.tick() is True

    assert harness._done.list() == []
    assert harness._failed.list() == []
    assert harness._healed.list() == []
    assert sorted(t.id for t in harness.archived.list()) == ["tsk_d", "tsk_f", "tsk_h"]
    assert all(t.status == ARCHIVED for t in harness.archived.list())


def test_the_sweep_never_touches_a_step_queue(tmp_path):
    seed(tmp_path)
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(NOW),
        retention_days=2,
    )
    step = next(iter(harness._step_queues))
    harness._step_queues[step].put(
        _settled("tsk_backlog", at="2026-06-01T12:00:00Z", status=step)
    )

    assert harness.retention_reconciler.tick() is False
    assert [t.id for t in harness._step_queues[step].list()] == ["tsk_backlog"]


def test_the_default_window_comes_from_the_reconciler_module(tmp_path):
    seed(tmp_path)
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(NOW),
    )
    harness._done.put(_settled("tsk_d", at="2026-07-27T12:00:00Z", status="done"))

    # 1 day old, inside the DEFAULT_RETENTION_DAYS=2 window
    assert DEFAULT_RETENTION_DAYS == 2
    assert harness.retention_reconciler.tick() is False
    assert [t.id for t in harness._done.list()] == ["tsk_d"]


def test_the_sweep_takes_the_task_off_the_board_but_leaves_it_gettable(tmp_path):
    """The whole reason for reusing `archived/`: `build()` composes the caller's
    sink with `ProjectionSink`, so the emitted `archived` event drives
    `BoardProjection.archive` with no extra wiring — off every column, still
    resolvable by id."""
    seed(tmp_path)
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(NOW),
        retention_days=2,
    )
    harness.projection.hydrate(
        inbox=harness._inbox,
        step_queues=harness._step_queues,
        done=harness._done,
        failed=harness._failed,
        archived=harness.archived,
        healed=harness._healed,
    )
    harness._done.put(_settled("tsk_old", at="2026-07-20T12:00:00Z", status="done"))
    harness.projection.apply("done", harness._done.list()[0])

    def on_board() -> list[str]:
        return [
            task.id
            for tab in harness.projection.snapshot().workflows
            for column in tab.columns
            for task in column.tasks
        ]

    assert "tsk_old" in on_board()

    harness.retention_reconciler.tick()

    assert "tsk_old" not in on_board()
    assert harness.projection.get("tsk_old") is not None


async def test_run_hosts_the_retention_loop_and_archives_an_old_settled_task(tmp_path):
    """The wiring's headline deliverable: `run()` itself gathers the retention
    loop, so an old settled task is archived by the running harness — not only
    by a `tick()` called by hand, as every test above does."""
    seed(tmp_path)
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(NOW),
        retention_days=2,
    )
    harness._done.put(_settled("tsk_old", at="2026-07-20T12:00:00Z", status="done"))

    stop = asyncio.Event()
    runner = asyncio.create_task(
        harness.run(poll_interval=0.01, reconcile_interval=0.01, stop=stop)
    )
    for _ in range(400):
        await asyncio.sleep(0.01)
        if (tmp_path / "archived" / "tsk_old.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=5.0)

    assert (tmp_path / "archived" / "tsk_old.json").exists()
    assert not (tmp_path / "done" / "tsk_old.json").exists()
    archived = Task.from_dict(
        json.loads((tmp_path / "archived" / "tsk_old.json").read_text())
    )
    assert archived.status == ARCHIVED
    assert archived.history[-1].actor == "retention"
