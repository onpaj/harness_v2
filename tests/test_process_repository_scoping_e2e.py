"""End-to-end: a process scoped to specific repositories, or to all of them,
on the real filesystem drivers (`FilesystemProcessAdmin`/`FilesystemProcess-
Repository`/`FilesystemRepositoryRegistry`), plus one pass through the editor
via the FastAPI test client — the acceptance criteria ask for the *editor* to
be exercised end-to-end, not only the admin backend.
"""

import json

from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.app import HarnessLayout
from harness.drivers.fs_processes import FilesystemProcessAdmin, FilesystemProcessRepository
from harness.drivers.fs_repos import FilesystemRepositoryRegistry
from harness.drivers.memory import FakeClock
from harness.ports.board import Board
from harness.ports.processes import ProcessValidationError
from tests.fakes import FakeBoardView

DEFINITION = {
    "name": "conflict-detector",
    "start": "fetch",
    "transitions": [
        {"from": "fetch", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "end"},
    ],
}


def _seed(tmp_path):
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    layout.repos.write_text(
        json.dumps(
            {
                "repo-a": str(tmp_path / "repo-a"),
                "repo-b": str(tmp_path / "repo-b"),
                "repo-c": str(tmp_path / "repo-c"),
            }
        )
    )
    (layout.workflows / "conflict-detector.json").write_text(json.dumps(DEFINITION))
    return layout


def test_scoping_to_specific_repositories_round_trips(tmp_path):
    layout = _seed(tmp_path)
    registry = FilesystemRepositoryRegistry(layout.repos)
    admin = FilesystemProcessAdmin(layout.workflows, registry)
    repository = FilesystemProcessRepository(layout.workflows, registry)

    admin.save_repositories("conflict-detector", ("repo-a", "repo-b"))
    process = repository.compile_process("conflict-detector")

    assert process.applies_to("repo-a")
    assert process.applies_to("repo-b")
    assert not process.applies_to("repo-c")


def test_scoping_to_all_repositories_round_trips(tmp_path):
    layout = _seed(tmp_path)
    registry = FilesystemRepositoryRegistry(layout.repos)
    admin = FilesystemProcessAdmin(layout.workflows, registry)
    repository = FilesystemProcessRepository(layout.workflows, registry)

    admin.save_repositories("conflict-detector", "*")
    process = repository.compile_process("conflict-detector")

    for name in registry.names():
        assert process.applies_to(name)


def test_unregistered_repository_is_rejected_on_save_and_compile(tmp_path):
    layout = _seed(tmp_path)
    registry = FilesystemRepositoryRegistry(layout.repos)
    admin = FilesystemProcessAdmin(layout.workflows, registry)
    repository = FilesystemProcessRepository(layout.workflows, registry)

    try:
        admin.save_repositories("conflict-detector", ("repo-a", "repo-z"))
        raised = False
    except ProcessValidationError as error:
        raised = True
        assert "repo-z" in str(error)

    assert raised

    # The file was left untouched by the failed save, so a direct write is
    # used here to exercise the compile-time validation path independently.
    raw = json.loads((layout.workflows / "conflict-detector.json").read_text())
    raw["repositories"] = ["repo-a", "repo-z"]
    (layout.workflows / "conflict-detector.json").write_text(json.dumps(raw))

    try:
        repository.compile_process("conflict-detector")
        raised = False
    except ProcessValidationError as error:
        raised = True
        assert "repo-z" in str(error)

    assert raised


def test_editor_saves_a_repository_scope_end_to_end(tmp_path):
    """Drives the save through the actual HTTP form the operator uses, not
    just the admin object directly."""
    layout = _seed(tmp_path)
    registry = FilesystemRepositoryRegistry(layout.repos)
    admin = FilesystemProcessAdmin(layout.workflows, registry)
    board = Board(revision=1, columns=())
    client = TestClient(
        create_app(view=FakeBoardView(board), processes=admin, repos=registry, clock=FakeClock())
    )

    edit_page = client.get("/processes/conflict-detector/edit")
    assert edit_page.status_code == 200
    assert "repo-a" in edit_page.text

    response = client.post(
        "/processes/conflict-detector/edit",
        data={"scope": "specific", "repositories": ["repo-a", "repo-c"]},
        follow_redirects=False,
    )
    assert response.status_code == 303

    repository = FilesystemProcessRepository(layout.workflows, registry)
    process = repository.compile_process("conflict-detector")
    assert process.applies_to("repo-a")
    assert process.applies_to("repo-c")
    assert not process.applies_to("repo-b")

    listing = client.get("/processes")
    assert "repo-a" in listing.text
    assert "repo-c" in listing.text


def test_editor_rejects_unknown_repository_with_inline_feedback(tmp_path):
    layout = _seed(tmp_path)
    registry = FilesystemRepositoryRegistry(layout.repos)
    admin = FilesystemProcessAdmin(layout.workflows, registry)
    board = Board(revision=1, columns=())
    client = TestClient(
        create_app(view=FakeBoardView(board), processes=admin, repos=registry, clock=FakeClock())
    )

    response = client.post(
        "/processes/conflict-detector/edit",
        data={"scope": "specific", "repositories": ["repo-a", "repo-z"]},
    )

    assert response.status_code == 422
    assert "repo-z" in response.text

    repository = FilesystemProcessRepository(layout.workflows, registry)
    process = repository.compile_process("conflict-detector")
    assert process.repositories == "*"  # unchanged: the failed save left the file untouched
