import html
import json

import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.fs_sources import FilesystemSourceAdmin
from harness.drivers.memory import FakeClock
from harness.ports.board import Board, BoardColumn, BoardTab
from tests.fakes import FakeBoardView

DEFINITION = json.dumps(
    {
        "kind": "github",
        "repository": "harness_v2",
        "select_label": "harness:todo",
        "target": {"workflow": "default"},
    },
    indent=2,
)


@pytest.fixture
def admin(tmp_path):
    return FilesystemSourceAdmin(tmp_path / "sources")


def _board_with_columns(*names: str) -> Board:
    columns = tuple(BoardColumn(name=n, tasks=()) for n in names)
    return Board(revision=1, workflows=(BoardTab(name="default", columns=columns),))


@pytest.fixture
def view():
    return FakeBoardView(_board_with_columns("todo", "plan", "review", "done", "failed"))


@pytest.fixture
def client(admin, view):
    return TestClient(create_app(view=view, clock=FakeClock(), source_admin=admin))


# --- JSON API ----------------------------------------------------------------


def test_list_sources_empty(client):
    response = client.get("/api/sources")
    assert response.status_code == 200
    assert response.json() == {"sources": []}


def test_list_sources_returns_every_name(client, admin):
    admin.write_raw("a", DEFINITION)
    admin.write_raw("b", DEFINITION)
    assert sorted(client.get("/api/sources").json()["sources"]) == ["a", "b"]


def test_get_source_returns_raw_text(client, admin):
    admin.write_raw("harness-issues", DEFINITION)
    response = client.get("/api/sources/harness-issues")
    assert response.status_code == 200
    assert response.text == DEFINITION


def test_get_unknown_source_returns_404(client):
    assert client.get("/api/sources/missing").status_code == 404


def test_put_source_creates_and_is_immediately_readable(client):
    response = client.put(
        "/api/sources/harness-issues",
        content=DEFINITION,
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"warnings": []}
    assert client.get("/api/sources/harness-issues").text == DEFINITION


def test_put_source_warns_about_an_unserved_target(client):
    text = json.dumps(
        {"kind": "github", "repository": "r", "target": {"workflow": "ghost"}}
    )
    response = client.put(
        "/api/sources/s", content=text, headers={"content-type": "application/json"}
    )
    assert response.status_code == 200
    warnings = response.json()["warnings"]
    assert any("ghost" in warning for warning in warnings)
    assert any("restart" in warning for warning in warnings)


def test_put_source_invalid_returns_422_and_writes_nothing(client, admin):
    response = client.put(
        "/api/sources/s",
        content=json.dumps({"kind": "github", "repository": "r"}),  # no target
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert "error" in response.json()
    assert admin.list() == ()


def test_delete_source_returns_204(client, admin):
    admin.write_raw("s", DEFINITION)
    assert client.delete("/api/sources/s").status_code == 204
    assert admin.list() == ()


def test_delete_unknown_source_returns_404(client):
    assert client.delete("/api/sources/missing").status_code == 404


# --- HTML admin --------------------------------------------------------------


def test_sources_list_page_shows_empty_state(client):
    assert "No sources defined yet." in client.get("/admin/sources").text


def test_new_source_page_is_reachable_before_dynamic_route(client):
    response = client.get("/admin/sources/new")
    assert response.status_code == 200
    assert '"kind": "github"' in html.unescape(response.text)


def test_source_editor_shows_exact_file_text(client, admin):
    admin.write_raw("harness-issues", DEFINITION)
    assert DEFINITION in html.unescape(client.get("/admin/sources/harness-issues").text)


def test_unknown_source_editor_returns_404(client):
    assert client.get("/admin/sources/missing").status_code == 404


def test_create_source_via_form(client, admin):
    response = client.post(
        "/admin/sources", data={"name": "harness-issues", "text": DEFINITION}
    )
    assert response.status_code == 200
    assert "saved" in response.text
    assert admin.read_raw("harness-issues") == DEFINITION


def test_create_source_with_existing_name_is_rejected(client, admin):
    admin.write_raw("harness-issues", DEFINITION)
    response = client.post(
        "/admin/sources", data={"name": "harness-issues", "text": DEFINITION}
    )
    assert response.status_code == 200
    assert "already exists" in response.text


def test_create_source_invalid_shows_inline_error_and_writes_nothing(client, admin):
    response = client.post(
        "/admin/sources",
        data={"name": "s", "text": json.dumps({"kind": "github", "repository": "r"})},
    )
    assert response.status_code == 200
    assert admin.list() == ()


def test_update_source_via_form(client, admin):
    admin.write_raw("s", DEFINITION)
    updated = json.dumps(
        {"kind": "github", "repository": "harness_v2", "target": {"step": "review"}}
    )
    response = client.post("/admin/sources/s", data={"text": updated})
    assert response.status_code == 200
    assert admin.read_raw("s") == updated


def test_update_source_invalid_leaves_file_untouched(client, admin):
    admin.write_raw("s", DEFINITION)
    broken = json.dumps({"kind": "github", "repository": "r"})  # no target
    response = client.post("/admin/sources/s", data={"text": broken})
    assert response.status_code == 200
    assert admin.read_raw("s") == DEFINITION


def test_delete_source_via_form_redirects_to_list(client, admin):
    admin.write_raw("s", DEFINITION)
    response = client.post("/admin/sources/s/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/sources"
    assert admin.list() == ()


def test_nav_includes_sources_tab(client):
    assert "/admin/sources" in client.get("/admin/sources").text
