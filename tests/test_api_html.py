import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.memory import FakeClock
from harness.models import HistoryEntry, Task
from harness.ports.board import Board, BoardColumn
from tests.fakes import FakeBoardView, FakeTaskControl

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

TITLED = Task(
    id="tsk_3",
    workflow_template="default",
    created="2026-07-19T10:00:02Z",
    repository="/Users/x/repos/my-repo",
    worktree="/Users/x/worktrees/wt_sacramento",
    status="development",
    data={"title": "Fix the login bug"},
)


BROKEN = Task(
    id="tsk_9",
    workflow_template="default",
    created="2026-07-19T10:00:03Z",
    status="failed",
    history=(
        HistoryEntry(
            at="2026-07-19T10:00:09Z",
            actor="dispatcher",
            from_step="plan",
            to_step="failed",
            reason="step 'plan' has no queue",
        ),
    ),
)


@pytest.fixture
def client() -> TestClient:
    board = Board(
        revision=9,
        columns=(
            BoardColumn(name="todo", tasks=()),
            BoardColumn(name="development", tasks=(WORKING, WAITING, TITLED)),
            BoardColumn(name="done", tasks=()),
            BoardColumn(name="failed", tasks=()),
        ),
    )
    # BROKEN is retrievable via get() (for the detail fragment) without cluttering
    # the rendered columns, so the board-rendering tests stay undisturbed.
    view = FakeBoardView(board, {"tsk_1": WORKING, "tsk_9": BROKEN})
    return TestClient(create_app(view=view, clock=FakeClock()))


def test_index_renders_board_shell(client):
    response = client.get("/")

    assert response.status_code == 200
    body = response.text
    assert "/static/htmx.min.js" in body
    assert "/static/sse.js" in body
    assert "/static/format-local-time.js" in body
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


def test_card_shows_title_instead_of_id_when_present(client):
    body = client.get("/fragment/board").text

    assert "Fix the login bug" in body
    # Titled task shows its title as the card label, not its raw id.
    assert ">tsk_3<" not in body


def test_card_falls_back_to_id_when_no_title(client):
    body = client.get("/fragment/board").text

    assert "tsk_1" in body
    assert "tsk_2" in body


def test_card_shows_repo_and_worktree_basename_not_path(client):
    body = client.get("/fragment/board").text

    assert "my-repo" in body
    assert "wt_sacramento" in body
    assert "/Users/x/" not in body


def test_card_shows_last_outcome(client):
    body = client.get("/fragment/board").text

    assert "request_changes" in body


def test_card_shows_time_in_state_only_when_history_exists(client):
    body = client.get("/fragment/board").text

    card_one, card_two = body.split("tsk_2", 1)
    assert "2026-07-19T10:00:05Z" in card_one
    assert "since" not in card_two


def test_card_time_is_a_time_element_for_client_side_localization(client):
    body = client.get("/fragment/board").text

    assert '<time datetime="2026-07-19T10:00:05Z"' in body
    assert 'title="2026-07-19T10:00:05Z UTC"' in body


def test_fragment_task_shows_metadata_and_history(client):
    body = client.get("/fragment/task/tsk_1").text

    assert "app-backend" in body
    assert "review" in body
    assert "development" in body
    assert "2026-07-19T10:00:05Z" in body
    assert "dispatcher" in body


def test_fragment_task_times_are_time_elements_for_client_side_localization(client):
    body = client.get("/fragment/task/tsk_1").text

    # "created" field
    assert '<time datetime="2026-07-19T10:00:00Z"' in body
    assert 'title="2026-07-19T10:00:00Z UTC"' in body
    # history table "time" column
    assert '<time datetime="2026-07-19T10:00:05Z"' in body
    assert 'title="2026-07-19T10:00:05Z UTC"' in body


def test_fragment_task_unknown_returns_404(client):
    assert client.get("/fragment/task/neznamy").status_code == 404


def test_static_files_are_served(client):
    assert client.get("/static/htmx.min.js").status_code == 200
    assert client.get("/static/format-local-time.js").status_code == 200


def test_no_endpoint_mutates(client):
    assert client.post("/api/board").status_code == 405
    assert client.delete("/api/tasks/tsk_1").status_code == 405


# --- Restart a failed task -------------------------------------------------


def _board_with_broken() -> Board:
    return Board(
        revision=9,
        columns=(
            BoardColumn(name="todo", tasks=()),
            BoardColumn(name="development", tasks=(WORKING,)),
            BoardColumn(name="failed", tasks=(BROKEN,)),
        ),
    )


def test_restart_button_shown_only_for_failed_task(client):
    failed_body = client.get("/fragment/task/tsk_9").text
    working_body = client.get("/fragment/task/tsk_1").text

    assert "/tasks/tsk_9/restart" in failed_body
    assert "Restart" in failed_body
    assert "/tasks/tsk_1/restart" not in working_body


def test_restart_invokes_control_and_returns_refreshed_fragment():
    view = FakeBoardView(_board_with_broken(), {"tsk_9": BROKEN})
    control = FakeTaskControl(result=True)
    api = TestClient(create_app(view=view, control=control, clock=FakeClock()))

    response = api.post("/tasks/tsk_9/restart")

    assert response.status_code == 200
    assert control.restarted == ["tsk_9"]
    assert "tsk_9" in response.text


def test_restart_returns_404_when_control_reports_nothing():
    view = FakeBoardView(_board_with_broken(), {"tsk_9": BROKEN})
    control = FakeTaskControl(result=False)
    api = TestClient(create_app(view=view, control=control, clock=FakeClock()))

    response = api.post("/tasks/tsk_9/restart")

    assert response.status_code == 404
    assert control.restarted == ["tsk_9"]


def test_restart_without_control_reports_nothing(client):
    # The default board is wired with the null control (create_app default).
    assert client.post("/tasks/tsk_9/restart").status_code == 404
