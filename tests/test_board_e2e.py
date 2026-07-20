import json

from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.app import Harness, HarnessLayout, build
from harness.drivers.system_clock import SystemClock
from harness.models import Task

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "plan"},
    ],
}

MAX_STEPS = 1000
"""A safeguard against an infinite loop, not a waiting mechanism.

A healthy test run needs a handful of steps; the extra hundreds give room
for a failure to be loud, not a silent ceiling that would look like a false
crash on a slower machine.
"""


def seed(tmp_path):
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


async def drive_until_quiet(harness: Harness, *, on_tick=None) -> int:
    """Drives the dispatcher and all consumers until there is nothing to do.

    No `sleep` or real-time polling — each step either processed something
    (and the loop continues) or didn't (and we're done). `on_tick` is called
    after every step, when the latest board state is visible.
    """
    for step in range(MAX_STEPS):
        acted = harness.dispatcher.tick()
        for consumer in harness.consumers:
            if await consumer.tick():
                acted = True
        if on_tick is not None:
            on_tick()
        if not acted:
            return step
    raise AssertionError(
        f"harness did not stop even after {MAX_STEPS} ticks of the dispatcher "
        "and consumers — suspected infinite loop in routing"
    )


def hydrate_from_queues(harness: Harness) -> None:
    """Recovery BEFORE hydration — otherwise tasks from `.processing/` are lost.

    `Harness.run()` does the same internally (see app.py); here we call it by
    hand, because `run()` itself runs through a real-time polling loop and the
    test replaces it with direct ticks of the dispatcher and consumers.
    """
    harness.recover()
    harness.projection.hydrate(
        inbox=harness._inbox,
        step_queues=harness._step_queues,
        done=harness._done,
        failed=harness._failed,
    )


async def test_board_follows_a_task_all_the_way_to_done(tmp_path):
    seed(tmp_path)
    harness = build(
        tmp_path, "default", delay=0.0, request_changes_once_at="review"
    )
    client = TestClient(create_app(view=harness.projection, clock=SystemClock()))
    task = Task(
        id="tsk_1", workflow_template="default", created="2026-07-19T10:00:00Z"
    )
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    hydrate_from_queues(harness)

    seen_columns = set()

    def observe() -> None:
        payload = client.get("/api/board").json()
        for column in payload["columns"]:
            if any(item["id"] == "tsk_1" for item in column["tasks"]):
                seen_columns.add(column["name"])

    await drive_until_quiet(harness, on_tick=observe)

    assert "plan" in seen_columns
    assert "review" in seen_columns
    assert "done" in seen_columns

    detail = client.get("/api/tasks/tsk_1").json()
    visited = [
        entry["to"] for entry in detail["history"] if entry["actor"] == "dispatcher"
    ]
    assert visited == ["plan", "review", "plan", "review", "end"]


async def test_failed_task_lands_in_failed_column(tmp_path):
    seed(tmp_path)
    harness = build(tmp_path, "default", delay=0.0)
    client = TestClient(create_app(view=harness.projection, clock=SystemClock()))
    broken = Task(
        id="tsk_2", workflow_template="neznamy", created="2026-07-19T10:00:00Z"
    )
    (tmp_path / "tasks" / "tsk_2.json").write_text(json.dumps(broken.to_dict()))

    hydrate_from_queues(harness)

    await drive_until_quiet(harness)

    columns = client.get("/api/board").json()["columns"]
    failed = next(item for item in columns if item["name"] == "failed")
    assert failed["tasks"], "task with an unknown workflow did not reach the failed column"
    assert failed["tasks"][0]["id"] == "tsk_2"


async def test_restart_moves_failed_task_back_to_todo(tmp_path):
    seed(tmp_path)
    harness = build(tmp_path, "default", delay=0.0)
    client = TestClient(
        create_app(
            view=harness.projection, control=harness.control, clock=SystemClock()
        )
    )
    # A task with an unknown workflow lands in `failed` on the first dispatch.
    broken = Task(
        id="tsk_3", workflow_template="neznamy", created="2026-07-19T10:00:00Z"
    )
    (tmp_path / "tasks" / "tsk_3.json").write_text(json.dumps(broken.to_dict()))

    hydrate_from_queues(harness)
    await drive_until_quiet(harness)

    def column_of(task_id: str) -> str | None:
        for column in client.get("/api/board").json()["columns"]:
            if any(item["id"] == task_id for item in column["tasks"]):
                return column["name"]
        return None

    assert column_of("tsk_3") == "failed"

    response = client.post("/tasks/tsk_3/restart")

    assert response.status_code == 200
    # Reset and back on the board as `todo`, ready for the dispatcher to pick up.
    assert column_of("tsk_3") == "todo"

    detail = client.get("/api/tasks/tsk_3").json()
    assert detail["status"] is None
    assert detail["history"][-1]["actor"] == "operator"
    assert detail["history"][-1]["reason"] == "restarted by operator"
