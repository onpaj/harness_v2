import json

import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.fs_agents import FilesystemAgentAdmin
from harness.drivers.memory import FakeClock
from harness.models import HistoryEntry, Task
from harness.ports.board import Board
from tests.fakes import FakeBoardView


def _task_handled_by(step, *, task_id="tsk_1", title=None, outcome="done", summary="did it"):
    return Task(
        id=task_id,
        created="2026-07-19T10:00:00Z",
        data={"title": title} if title else {},
        history=(
            HistoryEntry(
                at="2026-07-19T10:00:00Z",
                actor=f"consumer:{step}",
                from_step=step,
                to_step=None,
                outcome=outcome,
                summary=summary,
            ),
        ),
    )


def _client_with(admin, tasks):
    view = FakeBoardView(Board(revision=1, workflows=()), tasks)
    return TestClient(create_app(view=view, clock=FakeClock(), agent_admin=admin))


@pytest.fixture
def admin(tmp_path):
    return FilesystemAgentAdmin(tmp_path / "agents")


@pytest.fixture
def client(admin):
    view = FakeBoardView(Board(revision=1, workflows=()))
    return TestClient(create_app(view=view, clock=FakeClock(), agent_admin=admin))


# --- JSON API ----------------------------------------------------------------


def test_list_agents_empty(client):
    response = client.get("/api/agents")

    assert response.status_code == 200
    assert response.json() == {"agents": []}


def test_list_agents_returns_every_name(client, admin):
    admin.write("reviewer", _fields(prompt="you review"))
    admin.write("planner", _fields(prompt="you plan"))

    response = client.get("/api/agents")

    assert sorted(response.json()["agents"]) == ["planner", "reviewer"]


def test_get_agent_returns_the_spec(client, admin):
    admin.write("reviewer", _fields(prompt="you review", model="opus"))

    response = client.get("/api/agents/reviewer")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "reviewer"
    assert body["prompt"] == "you review"
    assert body["model"] == "opus"


def test_get_unknown_agent_returns_404(client):
    response = client.get("/api/agents/missing")

    assert response.status_code == 404


def test_put_agent_creates_and_is_immediately_readable(client):
    response = client.put(
        "/api/agents/planner",
        json={"prompt": "you plan", "allowed_outcomes": ["done"]},
    )

    assert response.status_code == 200
    assert client.get("/api/agents/planner").json()["prompt"] == "you plan"


def test_put_agent_without_prompt_returns_422_and_writes_nothing(client, tmp_path, admin):
    response = client.put("/api/agents/planner", json={"prompt": ""})

    assert response.status_code == 422
    assert "errors" in response.json()
    assert admin.list() == ()


def test_put_agent_with_invalid_outcome_returns_422(client):
    response = client.put(
        "/api/agents/weird", json={"prompt": "x", "allowed_outcomes": ["maybe"]}
    )

    assert response.status_code == 422


def test_put_agent_rejected_submission_leaves_existing_file_untouched(client, admin):
    admin.write("planner", _fields(prompt="original"))

    response = client.put("/api/agents/planner", json={"prompt": ""})

    assert response.status_code == 422
    assert admin.read("planner").prompt == "original"


def test_delete_agent_returns_204(client, admin):
    admin.write("planner", _fields(prompt="x"))

    response = client.delete("/api/agents/planner")

    assert response.status_code == 204
    assert admin.list() == ()


def test_delete_unknown_agent_returns_404(client):
    response = client.delete("/api/agents/missing")

    assert response.status_code == 404


def test_agent_history_json_returns_the_handlings(admin):
    client = _client_with(admin, {"tsk_1": _task_handled_by("planner", title="Fix bug")})

    body = client.get("/api/agents/planner/history").json()

    assert body["history"][0]["taskId"] == "tsk_1"
    assert body["history"][0]["title"] == "Fix bug"
    assert body["history"][0]["outcome"] == "done"
    assert body["history"][0]["summary"] == "did it"


def test_agent_history_json_of_unknown_agent_is_empty(client):
    assert client.get("/api/agents/ghost/history").json() == {"history": []}


# --- HTML admin ----------------------------------------------------------------


def test_agents_list_page_shows_empty_state(client):
    body = client.get("/admin/agents").text

    assert "No agents defined yet." in body


def test_agents_list_page_lists_names(client, admin):
    admin.write("reviewer", _fields(prompt="x"))

    body = client.get("/admin/agents").text

    assert "reviewer" in body
    assert "/admin/agents/reviewer" in body


def test_new_agent_page_is_reachable_before_dynamic_route(client):
    """Route registration order: /admin/agents/new must not be swallowed by
    the dynamic /admin/agents/{name} route."""
    response = client.get("/admin/agents/new")

    assert response.status_code == 200
    assert "New agent" in response.text


def test_agent_form_prefills_existing_fields(client, admin):
    admin.write(
        "reviewer",
        _fields(
            prompt="you are a reviewer",
            model="opus",
            allowed_outcomes=("done", "request_changes"),
        ),
    )

    body = client.get("/admin/agents/reviewer").text

    assert "you are a reviewer" in body
    assert 'value="opus"' in body
    assert "checked" in body


def test_unknown_agent_form_returns_404(client):
    response = client.get("/admin/agents/missing")

    assert response.status_code == 404


def test_create_agent_via_form(client, admin):
    response = client.post(
        "/admin/agents",
        data={
            "name": "planner",
            "prompt": "you plan",
            "allowed_tools": "Read, Grep",
            "allowed_outcomes": "done",
        },
    )

    assert response.status_code == 200
    assert "saved" in response.text
    spec = admin.read("planner")
    assert spec.prompt == "you plan"
    assert spec.allowed_tools == ("Read", "Grep")


def test_create_agent_with_existing_name_is_rejected(client, admin):
    admin.write("planner", _fields(prompt="original"))

    response = client.post(
        "/admin/agents", data={"name": "planner", "prompt": "new"}
    )

    assert response.status_code == 200
    assert "already exists" in response.text
    assert admin.read("planner").prompt == "original"


def test_create_agent_without_prompt_shows_inline_error(client):
    response = client.post("/admin/agents", data={"name": "planner", "prompt": ""})

    assert response.status_code == 200
    assert "prompt is required" in response.text


def test_update_agent_via_form(client, admin):
    admin.write("planner", _fields(prompt="v1"))

    response = client.post("/admin/agents/planner", data={"prompt": "v2"})

    assert response.status_code == 200
    assert admin.read("planner").prompt == "v2"


def test_update_agent_without_prompt_leaves_file_untouched(client, admin):
    admin.write("planner", _fields(prompt="v1"))

    response = client.post("/admin/agents/planner", data={"prompt": ""})

    assert response.status_code == 200
    assert "prompt is required" in response.text
    assert admin.read("planner").prompt == "v1"


def test_agent_form_shows_tasks_handled(admin):
    admin.write("planner", _fields(prompt="you plan"))
    client = _client_with(admin, {"tsk_1": _task_handled_by("planner", title="Fix bug")})

    body = client.get("/admin/agents/planner").text

    assert "Tasks handled" in body
    assert "Fix bug" in body


def test_agent_form_shows_empty_state_when_agent_never_ran(client, admin):
    admin.write("planner", _fields(prompt="x"))

    body = client.get("/admin/agents/planner").text

    assert "Tasks handled" in body
    assert "hasn't handled any tasks" in body


def test_new_agent_form_has_no_history_section(client):
    body = client.get("/admin/agents/new").text

    assert "Tasks handled" not in body


def test_delete_agent_via_form_redirects_to_list(client, admin):
    admin.write("planner", _fields(prompt="x"))

    response = client.post("/admin/agents/planner/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/agents"
    assert admin.list() == ()


def _fields(**kwargs):
    from harness.ports.agent_admin import AgentFields

    kwargs.setdefault("allowed_outcomes", ("done",))
    return AgentFields(**kwargs)
