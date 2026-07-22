import html
import json

import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.fs_workflows import FilesystemWorkflowAdmin
from harness.drivers.memory import FakeClock
from harness.ports.board import Board, BoardColumn, BoardTab
from tests.fakes import FakeBoardView

DEFINITION = json.dumps(
    {"start": "plan", "transitions": [{"from": "plan", "on": "done", "to": "review"}]},
    indent=2,
)


@pytest.fixture
def admin(tmp_path):
    return FilesystemWorkflowAdmin(tmp_path / "workflows")


def _board_with_columns(*names: str) -> Board:
    columns = tuple(BoardColumn(name=n, tasks=()) for n in names)
    return Board(revision=1, workflows=(BoardTab(name="default", columns=columns),))


@pytest.fixture
def view():
    return FakeBoardView(_board_with_columns("todo", "plan", "review", "done", "failed"))


@pytest.fixture
def client(admin, view):
    return TestClient(create_app(view=view, clock=FakeClock(), workflow_admin=admin))


# --- JSON API ----------------------------------------------------------------


def test_list_workflows_empty(client):
    response = client.get("/api/workflows")

    assert response.status_code == 200
    assert response.json() == {"workflows": []}


def test_list_workflows_returns_every_name(client, admin):
    admin.write_raw("default", DEFINITION)
    admin.write_raw("alt", DEFINITION)

    response = client.get("/api/workflows")

    assert sorted(response.json()["workflows"]) == ["alt", "default"]


def test_get_workflow_returns_raw_text(client, admin):
    admin.write_raw("default", DEFINITION)

    response = client.get("/api/workflows/default")

    assert response.status_code == 200
    assert response.text == DEFINITION


def test_get_unknown_workflow_returns_404(client):
    response = client.get("/api/workflows/missing")

    assert response.status_code == 404


def test_put_workflow_creates_and_is_immediately_readable(client):
    response = client.put(
        "/api/workflows/default",
        content=DEFINITION,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"warnings": []}
    assert client.get("/api/workflows/default").text == DEFINITION


def test_put_workflow_warns_about_a_new_step(client):
    text = json.dumps({"start": "plan", "transitions": [{"from": "plan", "on": "done", "to": "review_v2"}]})

    response = client.put(
        "/api/workflows/default", content=text, headers={"content-type": "application/json"}
    )

    assert response.status_code == 200
    warnings = response.json()["warnings"]
    assert any("review_v2" in warning for warning in warnings)
    assert any("restart" in warning for warning in warnings)


def test_put_workflow_invalid_json_returns_422_and_writes_nothing(client, admin):
    response = client.put(
        "/api/workflows/default",
        content="{not json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert "error" in response.json()
    assert admin.list() == ()


def test_put_workflow_missing_start_returns_422(client):
    response = client.put(
        "/api/workflows/default",
        content=json.dumps({"transitions": []}),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422


def test_put_workflow_rejected_submission_leaves_existing_file_untouched(client, admin):
    admin.write_raw("default", DEFINITION)

    response = client.put(
        "/api/workflows/default",
        content="{not json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert admin.read_raw("default") == DEFINITION


def test_delete_workflow_returns_204(client, admin):
    admin.write_raw("default", DEFINITION)

    response = client.delete("/api/workflows/default")

    assert response.status_code == 204
    assert admin.list() == ()


def test_delete_unknown_workflow_returns_404(client):
    response = client.delete("/api/workflows/missing")

    assert response.status_code == 404


# --- HTML admin ----------------------------------------------------------------


def test_workflows_list_page_shows_empty_state(client):
    body = client.get("/admin/workflows").text

    assert "No workflows defined yet." in body


def test_new_workflow_page_is_reachable_before_dynamic_route(client):
    response = client.get("/admin/workflows/new")

    assert response.status_code == 200
    assert '"start": ""' in html.unescape(response.text)


def test_workflow_editor_shows_exact_file_text(client, admin):
    admin.write_raw("default", DEFINITION)

    body = client.get("/admin/workflows/default").text

    assert DEFINITION in html.unescape(body)


def test_unknown_workflow_editor_returns_404(client):
    response = client.get("/admin/workflows/missing")

    assert response.status_code == 404


def test_create_workflow_via_form(client, admin):
    response = client.post(
        "/admin/workflows", data={"name": "default", "text": DEFINITION}
    )

    assert response.status_code == 200
    assert "saved" in response.text
    assert admin.read_raw("default") == DEFINITION


def test_create_workflow_with_existing_name_is_rejected(client, admin):
    admin.write_raw("default", DEFINITION)
    other = json.dumps({"start": "x", "transitions": []})

    response = client.post(
        "/admin/workflows", data={"name": "default", "text": other}
    )

    assert response.status_code == 200
    assert "already exists" in response.text
    assert admin.read_raw("default") == DEFINITION


def test_create_workflow_invalid_json_shows_inline_error_and_writes_nothing(client, admin):
    response = client.post(
        "/admin/workflows", data={"name": "default", "text": "{not json"}
    )

    assert response.status_code == 200
    assert admin.list() == ()


def test_update_workflow_via_form(client, admin):
    admin.write_raw("default", DEFINITION)
    updated = json.dumps({"start": "plan", "transitions": []})

    response = client.post("/admin/workflows/default", data={"text": updated})

    assert response.status_code == 200
    assert admin.read_raw("default") == updated


def test_update_workflow_shows_restart_warning(client, admin):
    admin.write_raw("default", DEFINITION)
    updated = json.dumps(
        {"start": "plan", "transitions": [{"from": "plan", "on": "done", "to": "review_v2"}]}
    )

    response = client.post("/admin/workflows/default", data={"text": updated})

    assert "review_v2" in response.text
    assert "restart" in response.text


def test_update_workflow_invalid_transition_leaves_file_untouched(client, admin):
    admin.write_raw("default", DEFINITION)
    broken = json.dumps({"start": "plan", "transitions": [{"from": "plan", "on": "done"}]})

    response = client.post("/admin/workflows/default", data={"text": broken})

    assert response.status_code == 200
    assert admin.read_raw("default") == DEFINITION


def test_delete_workflow_via_form_redirects_to_list(client, admin):
    admin.write_raw("default", DEFINITION)

    response = client.post("/admin/workflows/default/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/workflows"
    assert admin.list() == ()
