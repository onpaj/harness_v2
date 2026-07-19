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
"""Pojistka proti nekonečné smyčce, ne mechanismus čekání.

Zdravý běh testu potřebuje řádově jednotky kroků; stovky navíc je prostor
pro to, aby selhání bylo hlasité, ne tichý strop, který by na pomalejším
stroji vypadal jako falešný pád.
"""


def seed(tmp_path):
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


async def drive_until_quiet(harness: Harness, *, on_tick=None) -> int:
    """Prožene dispatcher a všechny consumery, dokud je co dělat.

    Žádné `sleep` ani polling na reálném čase — každý krok buď něco
    zpracoval (a smyčka pokračuje), nebo ne (a je hotovo). `on_tick` se
    zavolá po každém kroku, kdy je vidět nejnovější stav boardu.
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
        f"harness se nezastavil ani po {MAX_STEPS} tazích dispatcheru "
        "a consumerů — podezření na nekonečnou smyčku v routingu"
    )


def hydrate_from_queues(harness: Harness) -> None:
    """Recovery PŘED hydratací — jinak se ztratí tasky z `.processing/`.

    `Harness.run()` dělá totéž interně (viz app.py); tady to voláme ručně,
    protože `run()` samo běží přes real-time polling smyčku a test ho
    nahrazuje přímými tiky dispatcheru a consumerů.
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
    assert failed["tasks"], "task s neznámým workflow nedoputoval do sloupce failed"
    assert failed["tasks"][0]["id"] == "tsk_2"
