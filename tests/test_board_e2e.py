import asyncio
import json

from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.app import HarnessLayout, build
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


def seed(tmp_path):
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


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

    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    seen_columns = set()
    for _ in range(400):
        await asyncio.sleep(0.01)
        payload = client.get("/api/board").json()
        for column in payload["columns"]:
            if any(item["id"] == "tsk_1" for item in column["tasks"]):
                seen_columns.add(column["name"])
        if "done" in seen_columns:
            break
    stop.set()
    await runner

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

    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if harness.projection.get("tsk_2") is not None:
            break
    stop.set()
    await runner

    column = client.get("/api/board").json()["columns"]
    failed = next(item for item in column if item["name"] == "failed")
    assert failed["tasks"][0]["id"] == "tsk_2"
