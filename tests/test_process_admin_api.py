"""Process admin API — JSON routes under /api/processes and the HTML editor
under /admin/processes, mirroring `test_api_agents.py`.

The board carries one workflow tab with a step column so the target dropdown has
something to offer; the check/sink dropdowns come through the port.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.fs_processes import FilesystemProcessAdmin
from harness.drivers.memory import FakeClock
from harness.ports.board import Board, BoardColumn, BoardTab
from harness.ports.process_admin import ProcessFields
from tests.fakes import FakeBoardView


def _board() -> Board:
    tab = BoardTab(
        name="default",
        columns=(
            BoardColumn(name="todo", tasks=()),
            BoardColumn(name="cleanup", tasks=()),
            BoardColumn(name="done", tasks=()),
        ),
    )
    return Board(revision=1, workflows=(tab,))


@pytest.fixture
def admin(tmp_path):
    return FilesystemProcessAdmin(tmp_path / "processes")


@pytest.fixture
def client(admin):
    view = FakeBoardView(_board())
    return TestClient(create_app(view=view, clock=FakeClock(), process_admin=admin))


def _valid_form(**overrides) -> dict:
    form = {
        "name": "nightly",
        "interval": "1h",
        "check": "always",
        "target_kind": "workflow",
        "target": "default",
        "params": "",
        "sink_kind": "none",
        "dedup": "per-interval",
    }
    form.update(overrides)
    return form


# --- JSON API ----------------------------------------------------------------


def test_list_processes_empty(client):
    response = client.get("/api/processes")

    assert response.status_code == 200
    assert response.json() == {"processes": []}


def test_list_processes_returns_names(client, admin):
    admin.write("nightly", ProcessFields(interval="1h", check="always", target_kind="workflow", target="default"))

    assert client.get("/api/processes").json()["processes"] == ["nightly"]


def test_get_process_returns_fields(client, admin):
    admin.write("nightly", ProcessFields(interval="1h", check="always", target_kind="workflow", target="default"))

    body = client.get("/api/processes/nightly").json()

    assert body["interval"] == "1h"
    assert body["check"] == "always"
    assert body["target_kind"] == "workflow"
    assert body["target"] == "default"


def test_get_unknown_process_returns_404(client):
    assert client.get("/api/processes/missing").status_code == 404


def test_put_process_creates_and_is_readable(client):
    response = client.put(
        "/api/processes/nightly",
        json={
            "interval": "1h",
            "check": "always",
            "target_kind": "workflow",
            "target": "default",
        },
    )

    assert response.status_code == 200
    assert client.get("/api/processes/nightly").json()["interval"] == "1h"


def test_put_process_bad_interval_returns_422_and_writes_nothing(client, admin):
    response = client.put(
        "/api/processes/nightly",
        json={
            "interval": "1x",
            "check": "always",
            "target_kind": "workflow",
            "target": "default",
        },
    )

    assert response.status_code == 422
    assert "interval" in response.json()["errors"]
    assert admin.list() == ()


def test_delete_process(client, admin):
    admin.write("nightly", ProcessFields(interval="1h", check="always", target_kind="workflow", target="default"))

    assert client.delete("/api/processes/nightly").status_code == 204
    assert admin.list() == ()


def test_delete_unknown_process_returns_404(client):
    assert client.delete("/api/processes/missing").status_code == 404


# --- HTML admin --------------------------------------------------------------


def test_processes_list_page_shows_empty_state(client):
    assert "No processes defined yet." in client.get("/admin/processes").text


def test_processes_list_page_lists_names(client, admin):
    admin.write("nightly", ProcessFields(interval="1h", check="always", target_kind="workflow", target="default"))

    body = client.get("/admin/processes").text

    assert "nightly" in body
    assert "/admin/processes/nightly" in body


def test_new_process_page_is_reachable_before_dynamic_route(client):
    response = client.get("/admin/processes/new")

    assert response.status_code == 200
    assert "New process" in response.text


def test_new_process_page_populates_dropdowns(client):
    body = client.get("/admin/processes/new").text

    # check options come through the port
    assert "always" in body
    assert "disk-threshold" in body
    # target options come from the board (a step column + the workflow tab)
    assert "cleanup" in body
    assert "default" in body


def test_create_process_via_form(client, admin):
    response = client.post("/admin/processes", data=_valid_form())

    assert response.status_code == 200
    assert "saved" in response.text
    assert admin.read("nightly").interval == "1h"


def test_create_process_needs_a_name(client, admin):
    response = client.post("/admin/processes", data=_valid_form(name=""))

    assert response.status_code == 200
    assert "name is required" in response.text
    assert admin.list() == ()


def test_create_process_with_existing_name_is_rejected(client, admin):
    admin.write("nightly", ProcessFields(interval="1h", check="always", target_kind="workflow", target="default"))

    response = client.post("/admin/processes", data=_valid_form(name="nightly"))

    assert response.status_code == 200
    assert "already exists" in response.text


def test_create_process_bad_interval_shows_field_error_and_writes_nothing(
    client, admin
):
    response = client.post(
        "/admin/processes", data=_valid_form(name="nightly", interval="1x")
    )

    assert response.status_code == 200
    assert "field-error" in response.text
    assert admin.list() == ()


def test_create_cron_process_via_form(client, admin):
    response = client.post(
        "/admin/processes",
        data=_valid_form(name="weekly-review", cadence="cron", cron="0 6 * * 1"),
    )

    assert response.status_code == 200
    assert "saved" in response.text
    fields = admin.read("weekly-review")
    assert fields.cadence == "cron"
    assert fields.cron == "0 6 * * 1"


def test_create_process_bad_cron_shows_field_error_and_writes_nothing(client, admin):
    response = client.post(
        "/admin/processes",
        data=_valid_form(name="bad-cron", cadence="cron", cron="0 6 31 2 *"),
    )

    assert response.status_code == 200
    assert "field-error" in response.text
    assert admin.list() == ()


def test_create_process_bad_params_json_shows_field_error(client, admin):
    response = client.post(
        "/admin/processes",
        data=_valid_form(name="nightly", check="disk-threshold", params="{not json"),
    )

    assert response.status_code == 200
    assert "field-error" in response.text
    assert admin.list() == ()


def test_edit_process_page_shows_current_values(client, admin):
    admin.write("nightly", ProcessFields(interval="2h", check="always", target_kind="step", target="cleanup"))

    body = client.get("/admin/processes/nightly").text

    assert 'value="2h"' in body
    assert "Process: nightly" in body


def test_edit_process_page_shows_cron_value_and_no_disabled_inputs(client, admin):
    admin.write(
        "weekly-review",
        ProcessFields(
            cadence="cron", cron="0 6 * * 1", check="always", target_kind="workflow", target="default"
        ),
    )

    body = client.get("/admin/processes/weekly-review").text

    assert 'value="0 6 * * 1"' in body
    assert 'name="cadence" value="cron"\n                   checked' in body
    # Both cadence panels must always submit — neither input may be `disabled`,
    # only visually hidden via the wrapping div, or the hidden panel's value
    # would silently drop from the POST body on a toggle-then-submit.
    assert "disabled" not in body


def test_update_process_via_form(client, admin):
    admin.write("nightly", ProcessFields(interval="1h", check="always", target_kind="workflow", target="default"))

    response = client.post(
        "/admin/processes/nightly", data=_valid_form(interval="6h")
    )

    assert response.status_code == 200
    assert admin.read("nightly").interval == "6h"


def test_delete_process_via_form_redirects(client, admin):
    admin.write("nightly", ProcessFields(interval="1h", check="always", target_kind="workflow", target="default"))

    response = client.post(
        "/admin/processes/nightly/delete", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/processes"
    assert admin.list() == ()


def test_nav_includes_processes_link(client):
    assert "/admin/processes" in client.get("/admin/agents").text


# --- no process_admin configured ---------------------------------------------


def test_app_without_process_admin_boots_and_lists_nothing(tmp_path):
    view = FakeBoardView(_board())
    app = create_app(view=view, clock=FakeClock())
    plain = TestClient(app)

    assert plain.get("/api/processes").json() == {"processes": []}
    assert "No processes defined yet." in plain.get("/admin/processes").text
