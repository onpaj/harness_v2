import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.drivers.fs_processes import FilesystemProcessAdmin, FilesystemProcessRepository
from harness.drivers.memory import FakeClock, MemoryRepositoryRegistry
from harness.ports.board import Board
from tests.fakes import FakeBoardView

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "end"},
    ],
}


def _write(root, name, extra=None):
    payload = dict(DEFINITION, name=name)
    if extra:
        payload.update(extra)
    (root / f"{name}.json").write_text(json.dumps(payload))


@pytest.fixture
def registry():
    return MemoryRepositoryRegistry(
        {"repo-a": Path("/repos/repo-a"), "repo-b": Path("/repos/repo-b")}
    )


@pytest.fixture
def client(tmp_path, registry):
    _write(tmp_path, "conflict-detector", {"repositories": ["repo-a"]})
    _write(tmp_path, "disk-threshold", {"repositories": "*"})
    admin = FilesystemProcessAdmin(tmp_path, registry)
    board = Board(revision=1, columns=())
    app = create_app(
        view=FakeBoardView(board), processes=admin, repos=registry, clock=FakeClock()
    )
    return TestClient(app)


def test_list_processes_shows_each_process_and_its_scope(client):
    body = client.get("/processes").text

    assert "conflict-detector" in body
    assert "repo-a" in body
    assert "disk-threshold" in body
    assert "all repositories" in body


def test_list_processes_flags_stale_repository(tmp_path, registry):
    _write(tmp_path, "release-notes", {"repositories": ["repo-a", "repo-z"]})
    admin = FilesystemProcessAdmin(tmp_path, registry)
    board = Board(revision=1, columns=())
    api = TestClient(
        create_app(view=FakeBoardView(board), processes=admin, repos=registry, clock=FakeClock())
    )

    body = api.get("/processes").text

    assert "repo-z" in body
    assert "unknown" in body.lower()


def test_edit_form_shows_known_repositories_and_current_selection(client):
    body = client.get("/processes/conflict-detector/edit").text

    assert "repo-a" in body
    assert "repo-b" in body
    assert 'value="repo-a" checked' in body or "checked" in body


def test_edit_unknown_process_returns_404(client):
    assert client.get("/processes/unknown/edit").status_code == 404


def test_save_specific_repositories_persists_and_redirects(client, tmp_path):
    response = client.post(
        "/processes/conflict-detector/edit",
        data={"scope": "specific", "repositories": ["repo-a", "repo-b"]},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/processes"
    raw = json.loads((tmp_path / "conflict-detector.json").read_text())
    assert raw["repositories"] == ["repo-a", "repo-b"]


def test_save_all_repositories_persists_wildcard(client, tmp_path):
    response = client.post(
        "/processes/conflict-detector/edit",
        data={"scope": "all"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    raw = json.loads((tmp_path / "conflict-detector.json").read_text())
    assert raw["repositories"] == "*"


def test_save_unknown_repository_shows_inline_error_and_preserves_selection(client):
    response = client.post(
        "/processes/conflict-detector/edit",
        data={"scope": "specific", "repositories": ["repo-a", "repo-z"]},
    )

    assert response.status_code == 422
    assert "repo-z" in response.text
    assert 'value="repo-z"' in response.text


def test_save_empty_specific_selection_shows_inline_error(client):
    response = client.post(
        "/processes/conflict-detector/edit",
        data={"scope": "specific"},
    )

    assert response.status_code == 422
    assert "no repositories selected" in response.text


def test_save_unknown_process_returns_404(client):
    response = client.post(
        "/processes/unknown/edit",
        data={"scope": "all"},
    )

    assert response.status_code == 404


def test_processes_editor_without_wiring_lists_nothing():
    """Backward compatibility: a board created without `processes`/`repos`
    still serves a (empty) editor instead of failing to start."""
    board = Board(revision=1, columns=())
    api = TestClient(create_app(view=FakeBoardView(board), clock=FakeClock()))

    response = api.get("/processes")

    assert response.status_code == 200
    assert "conflict-detector" not in response.text


def test_compile_process_after_save_reflects_new_scope(tmp_path, registry):
    _write(tmp_path, "conflict-detector")
    admin = FilesystemProcessAdmin(tmp_path, registry)
    board = Board(revision=1, columns=())
    api = TestClient(
        create_app(view=FakeBoardView(board), processes=admin, repos=registry, clock=FakeClock())
    )

    api.post(
        "/processes/conflict-detector/edit",
        data={"scope": "specific", "repositories": ["repo-b"]},
        follow_redirects=False,
    )

    process = FilesystemProcessRepository(tmp_path, registry).compile_process(
        "conflict-detector"
    )
    assert process.applies_to("repo-b")
    assert not process.applies_to("repo-a")
