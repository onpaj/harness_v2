import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.memory import FakeClock
from harness.models import HistoryEntry, Task
from harness.ports.board import Board, BoardColumn
from tests.fakes import FakeBoardView

WORKING = Task(
    id="tsk_1",
    workflow_template="default",
    created="2026-07-19T10:00:00Z",
    repository="app-backend",
    status="development",
    last_outcome="request_changes",
    lock_id="lck_1",
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

WAITING = Task(
    id="tsk_2",
    workflow_template="default",
    created="2026-07-19T10:00:01Z",
    status="development",
)


@pytest.fixture
def client() -> TestClient:
    board = Board(
        revision=9,
        columns=(
            BoardColumn(name="development", tasks=(WORKING, WAITING)),
            BoardColumn(name="done", tasks=()),
            BoardColumn(name="failed", tasks=()),
        ),
    )
    view = FakeBoardView(board, {"tsk_1": WORKING})
    return TestClient(create_app(view=view, clock=FakeClock()))


def test_index_renders_board_shell(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.text
    assert "/static/htmx.min.js" in body
    assert "/static/sse.js" in body
    assert "/api/events" in body
    assert "https://" not in body


def test_index_contains_columns(client):
    body = client.get("/").text

    assert "development" in body
    assert "done" in body
    assert "failed" in body


def test_fragment_board_lists_cards_in_order(client):
    body = client.get("/fragment/board").text

    assert body.index("tsk_1") < body.index("tsk_2")
    assert "app-backend" in body


def test_card_shows_working_badge_only_when_locked(client):
    body = client.get("/fragment/board").text

    card_one, card_two = body.split("tsk_2", 1)
    assert "processing" in card_one
    assert "processing" not in card_two


def test_card_shows_last_outcome(client):
    body = client.get("/fragment/board").text

    assert "request_changes" in body


def test_card_shows_time_in_state_only_when_history_exists(client):
    body = client.get("/fragment/board").text

    card_one, card_two = body.split("tsk_2", 1)
    assert "2026-07-19T10:00:05Z" in card_one
    assert "since" not in card_two


def test_fragment_task_shows_metadata_and_history(client):
    body = client.get("/fragment/task/tsk_1").text

    assert "app-backend" in body
    assert "review" in body
    assert "development" in body
    assert "2026-07-19T10:00:05Z" in body
    assert "dispatcher" in body


def test_fragment_task_unknown_returns_404(client):
    assert client.get("/fragment/task/neznamy").status_code == 404


def test_static_files_are_served(client):
    assert client.get("/static/htmx.min.js").status_code == 200


def test_no_endpoint_mutates(client):
    assert client.post("/api/board").status_code == 405
    assert client.delete("/api/tasks/tsk_1").status_code == 405
