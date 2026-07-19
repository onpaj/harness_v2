import pytest
from tests.fakes import FakeBoardView
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.memory import FakeClock
from harness.models import HistoryEntry, Task
from harness.ports.board import Board, BoardColumn


def make_task(task_id="tsk_1", **kwargs) -> Task:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        **kwargs,
    )


@pytest.fixture
def client() -> TestClient:
    task = make_task(status="plan", repository="app-backend")
    board = Board(
        revision=4,
        columns=(
            BoardColumn(name="plan", tasks=(task,)),
            BoardColumn(name="done", tasks=()),
        ),
    )
    view = FakeBoardView(board, {"tsk_1": task})
    return TestClient(create_app(view=view, clock=FakeClock()))


def test_board_endpoint_returns_columns(client):
    response = client.get("/api/board")

    assert response.status_code == 200
    payload = response.json()
    assert payload["revision"] == 4
    assert [column["name"] for column in payload["columns"]] == ["plan", "done"]
    assert payload["columns"][0]["tasks"][0]["id"] == "tsk_1"


def test_task_endpoint_returns_camelcase_task(client):
    response = client.get("/api/tasks/tsk_1")

    assert response.status_code == 200
    assert response.json()["workflowTemplate"] == "default"


def test_unknown_task_returns_404(client):
    response = client.get("/api/tasks/neznamy")

    assert response.status_code == 404
    assert "neznamy" in response.json()["detail"]


def test_task_endpoint_includes_history():
    task = make_task(
        status="development",
        history=(
            HistoryEntry(
                at="2026-07-19T10:00:05Z",
                actor="dispatcher",
                from_step="review",
                to_step="development",
                outcome="request_changes",
            ),
        ),
    )
    view = FakeBoardView(Board(revision=1, columns=()), {"tsk_1": task})
    client = TestClient(create_app(view=view, clock=FakeClock()))

    entry = client.get("/api/tasks/tsk_1").json()["history"][0]

    assert entry["from"] == "review"
    assert entry["to"] == "development"
    assert entry["outcome"] == "request_changes"
