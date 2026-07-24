"""Process admin API — JSON routes under /api/processes and the HTML editor
under /admin/processes, mirroring `test_api_agents.py`.

The board carries one workflow tab with a step column so the target dropdown has
something to offer; the check/sink dropdowns come through the port.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.fs_processes import FilesystemProcessAdmin
from harness.drivers.fs_repos import FilesystemRepositoryRegistry
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


def test_process_form_embeds_action_definitions_as_data(client):
    """The action definitions (labels + parameters) are served as JSON the
    script interprets — the form has nothing hardcoded per action. The old
    template baked a `PARAM_SPECS` object and a `check_meta` map into the page;
    now those come from the port."""
    import json

    body = client.get("/admin/processes/new").text

    # the embedded, machine-readable definitions the script builds its fields from
    assert 'id="check-specs-data"' in body
    marker = 'id="check-specs-data">'
    start = body.index(marker) + len(marker)
    end = body.index("</script>", start)
    specs = json.loads(body[start:end])

    by_name = {spec["name"]: spec for spec in specs}
    disk = by_name["disk-threshold"]
    assert disk["label"] == "Disk threshold"
    assert {param["key"] for param in disk["params"]} == {"path", "percent"}
    percent = next(p for p in disk["params"] if p["key"] == "percent")
    assert percent["type"] == "number"

    # the hardcoded structures the template used to carry are gone
    assert "var PARAM_SPECS = {\n" not in body
    assert "check_meta" not in body


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
    # would silently drop from the POST body on a toggle-then-submit. (The
    # Repository section's `<select>` is legitimately `disabled` here — no
    # repository is set — but that's a UI affordance only: the server decides
    # "all vs. specific" from `repo_scope`, never from the select's own
    # disabled-ness, so it's fine for that field alone to be disabled.)
    interval_tag = re.search(r'<input[^>]*id="interval"[^>]*>', body).group()
    cron_tag = re.search(r'<input[^>]*id="cron"[^>]*>', body).group()
    assert "disabled" not in interval_tag
    assert "disabled" not in cron_tag


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


def test_edit_page_renders_a_check_absent_from_the_registry(client, admin, tmp_path):
    """A hand-written process naming a check the running registry doesn't know
    still renders — with a generic prepended card and the raw-JSON editor for
    its params — so editing never hides the current definition."""
    import json

    (admin._root / "mystery.json").write_text(
        json.dumps(
            {
                "trigger": {"interval": "1h"},
                "action": {"check": "custom-thing", "params": {"foo": "bar"}},
                "target": {"workflow": "default"},
            }
        ),
        encoding="utf-8",
    )

    body = client.get("/admin/processes/mystery").text

    assert body.count("value=\"custom-thing\"") == 1  # the prepended card
    assert "always" in body  # the real registry options are still offered


def test_nav_includes_processes_link(client):
    assert "/admin/processes" in client.get("/admin/agents").text


# --- no process_admin configured ---------------------------------------------


# --- repository field ---------------------------------------------------------


def test_put_process_json_carries_repository(client):
    response = client.put(
        "/api/processes/nightly",
        json={
            "interval": "1h",
            "check": "always",
            "target_kind": "workflow",
            "target": "default",
            "repository": "harness_v2",
        },
    )

    assert response.status_code == 200
    assert client.get("/api/processes/nightly").json()["repository"] == "harness_v2"


def test_get_process_defaults_repository_to_empty_string(client, admin):
    admin.write(
        "nightly",
        ProcessFields(interval="1h", check="always", target_kind="workflow", target="default"),
    )

    assert client.get("/api/processes/nightly").json()["repository"] == ""


def test_create_process_via_form_with_specific_repository(client, admin):
    response = client.post(
        "/admin/processes",
        data=_valid_form(repo_scope="specific", repository="harness_v2"),
    )

    assert response.status_code == 200
    assert admin.read("nightly").repository == "harness_v2"


def test_create_process_via_form_all_repositories_ignores_stale_select_value(client, admin):
    # Even if a stale/leftover `repository` value rode along in the POST body
    # (e.g. a disabled <select> a misbehaving browser submitted anyway), the
    # server decides "all" from `repo_scope` alone.
    response = client.post(
        "/admin/processes",
        data=_valid_form(repo_scope="all", repository="harness_v2"),
    )

    assert response.status_code == 200
    assert admin.read("nightly").repository == ""


def test_create_process_via_form_no_repo_scope_defaults_to_all(client, admin):
    response = client.post("/admin/processes", data=_valid_form())

    assert response.status_code == 200
    assert admin.read("nightly").repository == ""


def test_edit_process_page_redisplays_specific_scope_after_a_params_error(
    client, admin
):
    # The params-JSON error path reconstructs fields from every posted key
    # (routes.py's generic dict reconstruction), so a `repo_scope=specific`
    # submission must still render "Specific" checked, not silently reset to
    # "All repositories".
    response = client.post(
        "/admin/processes",
        data=_valid_form(
            name="nightly",
            check="disk-threshold",
            params="{not json",
            repo_scope="specific",
            repository="harness_v2",
        ),
    )

    assert response.status_code == 200
    assert "field-error" in response.text
    specific_radio = re.search(
        r'<input type="radio" name="repo_scope" value="specific"[^>]*>', response.text
    ).group()
    assert "checked" in specific_radio


def test_process_admin_repository_options_render_in_new_process_page(tmp_path):
    repos_config = tmp_path / "repos.json"
    repos_config.write_text(json.dumps({"harness_v2": "/repos/harness_v2"}), encoding="utf-8")
    admin = FilesystemProcessAdmin(
        tmp_path / "processes", registry=FilesystemRepositoryRegistry(repos_config)
    )
    view = FakeBoardView(_board())
    app_client = TestClient(
        create_app(view=view, clock=FakeClock(), process_admin=admin)
    )

    body = app_client.get("/admin/processes/new").text

    assert "harness_v2" in body
    assert "All repositories" in body


def test_app_without_process_admin_boots_and_lists_nothing(tmp_path):
    view = FakeBoardView(_board())
    app = create_app(view=view, clock=FakeClock())
    plain = TestClient(app)

    assert plain.get("/api/processes").json() == {"processes": []}
    assert "No processes defined yet." in plain.get("/admin/processes").text
