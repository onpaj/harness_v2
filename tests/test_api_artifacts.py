import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.memory import FakeClock, MemoryArtifactStore
from harness.models import Task
from harness.ports.board import Board
from tests.fakes import FakeBoardView

SOURCE = Path(__file__).resolve().parents[1] / "src" / "harness"


def make_task(task_id="tsk_1") -> Task:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="plan",
    )


@pytest.fixture
def store() -> MemoryArtifactStore:
    store = MemoryArtifactStore()
    slot = store.begin("tsk_1", "plan")
    slot.put("plan.md", "the plan")
    slot.put("notes.txt", "some notes")
    # another task must not leak into the tsk_1 listing
    other = store.begin("tsk_2", "plan")
    other.put("plan.md", "not mine")
    return store


@pytest.fixture
def client(store) -> TestClient:
    task = make_task()
    board = Board(revision=1, columns=())
    view = FakeBoardView(board, {"tsk_1": task})
    return TestClient(create_app(view=view, artifacts=store, clock=FakeClock()))


def test_list_returns_task_artifacts_as_json(client):
    response = client.get("/api/tasks/tsk_1/artifacts")

    assert response.status_code == 200
    refs = response.json()["artifacts"]
    assert {(r["step"], r["attempt"], r["name"]) for r in refs} == {
        ("plan", 0, "plan.md"),
        ("plan", 0, "notes.txt"),
    }


def test_list_is_empty_for_unknown_task(client):
    assert client.get("/api/tasks/nic/artifacts").json() == {"artifacts": []}


def test_content_route_returns_stored_text(client):
    response = client.get("/api/tasks/tsk_1/artifacts/plan/0/plan.md")

    assert response.status_code == 200
    assert response.text == "the plan"
    assert response.headers["content-type"].startswith("text/plain")


def test_content_route_404s_for_missing_artifact(client):
    assert client.get("/api/tasks/tsk_1/artifacts/plan/0/chybi.md").status_code == 404
    # a wrong attempt is also a 404
    assert client.get("/api/tasks/tsk_1/artifacts/plan/9/plan.md").status_code == 404


def test_fragment_task_lists_artifacts_with_links(store):
    view = FakeBoardView(Board(revision=1, columns=()), {"tsk_1": make_task()})
    client = TestClient(create_app(view=view, artifacts=store, clock=FakeClock()))

    body = client.get("/fragment/task/tsk_1").text

    assert "plan.md" in body
    assert "/api/tasks/tsk_1/artifacts/plan/0/plan.md" in body


def test_create_app_works_without_artifacts():
    view = FakeBoardView(Board(revision=1, columns=()), {"tsk_1": make_task()})
    client = TestClient(create_app(view=view, clock=FakeClock()))

    assert client.get("/api/tasks/tsk_1/artifacts").json() == {"artifacts": []}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_api_imports_only_the_artifact_port_not_drivers():
    """api/ may know artifacts only through the port, never through a driver."""
    for path in (SOURCE / "api").rglob("*.py"):
        modules = _imported_modules(path)
        assert not any(m.startswith("harness.drivers") for m in modules), (
            f"{path.name} imports a driver"
        )
