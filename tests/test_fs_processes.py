import json

import pytest

from harness.drivers.fs_processes import FilesystemProcessAdmin, FilesystemProcessRepository
from harness.drivers.memory import MemoryRepositoryRegistry
from harness.ports.processes import ProcessValidationError
from harness.ports.workflows import WorkflowNotFound

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "end"},
    ],
}


def _write(tmp_path, name, extra=None):
    payload = dict(DEFINITION, name=name)
    if extra:
        payload.update(extra)
    (tmp_path / f"{name}.json").write_text(json.dumps(payload))


def _registry(**repos):
    return MemoryRepositoryRegistry({name: tmp_path for name, tmp_path in repos.items()})


# --- FilesystemProcessRepository.compile_process ---------------------------


def test_compile_process_defaults_to_all_repositories_when_absent(tmp_path):
    _write(tmp_path, "default")
    repository = FilesystemProcessRepository(tmp_path, _registry())

    process = repository.compile_process("default")

    assert process.repositories == "*"
    assert process.workflow.start == "plan"


def test_compile_process_accepts_specific_known_repositories(tmp_path):
    _write(tmp_path, "default", {"repositories": ["repo-a", "repo-b"]})
    repository = FilesystemProcessRepository(
        tmp_path, MemoryRepositoryRegistry({"repo-a": tmp_path, "repo-b": tmp_path})
    )

    process = repository.compile_process("default")

    assert process.repositories == ("repo-a", "repo-b")
    assert process.applies_to("repo-a")
    assert not process.applies_to("repo-z")


def test_compile_process_accepts_all_wildcard(tmp_path):
    _write(tmp_path, "default", {"repositories": "*"})
    repository = FilesystemProcessRepository(
        tmp_path, MemoryRepositoryRegistry({"repo-a": tmp_path})
    )

    process = repository.compile_process("default")

    assert process.applies_to("repo-a")
    assert process.applies_to("any-other-repo")


def test_compile_process_rejects_empty_repositories(tmp_path):
    _write(tmp_path, "default", {"repositories": []})
    repository = FilesystemProcessRepository(tmp_path, _registry())

    with pytest.raises(ProcessValidationError, match="no repositories selected"):
        repository.compile_process("default")


def test_compile_process_rejects_unknown_repository(tmp_path):
    _write(tmp_path, "default", {"repositories": ["repo-a", "repo-z"]})
    repository = FilesystemProcessRepository(
        tmp_path, MemoryRepositoryRegistry({"repo-a": tmp_path})
    )

    with pytest.raises(ProcessValidationError, match="repo-z") as excinfo:
        repository.compile_process("default")
    assert "repo-a" in str(excinfo.value)


def test_compile_process_missing_file_raises_workflow_not_found(tmp_path):
    repository = FilesystemProcessRepository(tmp_path, _registry())

    with pytest.raises(WorkflowNotFound):
        repository.compile_process("unknown")


# --- FilesystemProcessAdmin --------------------------------------------------


def test_list_processes_lists_json_files(tmp_path):
    _write(tmp_path, "alpha")
    _write(tmp_path, "beta")
    admin = FilesystemProcessAdmin(tmp_path, _registry())

    assert admin.list_processes() == ["alpha", "beta"]


def test_load_process_returns_summary(tmp_path):
    _write(tmp_path, "default", {"repositories": ["repo-a"]})
    admin = FilesystemProcessAdmin(tmp_path, MemoryRepositoryRegistry({"repo-a": tmp_path}))

    summary = admin.load_process("default")

    assert summary.name == "default"
    assert summary.start == "plan"
    assert summary.steps == ("plan", "review")
    assert summary.repositories == ("repo-a",)


def test_load_process_is_lenient_about_deregistered_repository(tmp_path):
    """The one non-obvious invariant: load must not raise even when the stored
    scope no longer matches the registry, or the editor could never open the
    process to fix the drift."""
    _write(tmp_path, "default", {"repositories": ["repo-z"]})
    admin = FilesystemProcessAdmin(tmp_path, _registry())  # repo-z not registered

    summary = admin.load_process("default")

    assert summary.repositories == ("repo-z",)


def test_load_process_missing_raises(tmp_path):
    admin = FilesystemProcessAdmin(tmp_path, _registry())

    with pytest.raises(WorkflowNotFound):
        admin.load_process("unknown")


def test_save_repositories_persists_specific_repos(tmp_path):
    _write(tmp_path, "default")
    registry = MemoryRepositoryRegistry({"repo-a": tmp_path, "repo-b": tmp_path})
    admin = FilesystemProcessAdmin(tmp_path, registry)

    admin.save_repositories("default", ("repo-a", "repo-b"))

    repository = FilesystemProcessRepository(tmp_path, registry)
    assert repository.compile_process("default").repositories == ("repo-a", "repo-b")


def test_save_repositories_persists_all_wildcard(tmp_path):
    _write(tmp_path, "default", {"repositories": ["repo-a"]})
    registry = MemoryRepositoryRegistry({"repo-a": tmp_path})
    admin = FilesystemProcessAdmin(tmp_path, registry)

    admin.save_repositories("default", "*")

    repository = FilesystemProcessRepository(tmp_path, registry)
    process = repository.compile_process("default")
    assert process.repositories == "*"
    assert process.applies_to("repo-a")


def test_save_repositories_preserves_other_keys(tmp_path):
    _write(tmp_path, "default")
    admin = FilesystemProcessAdmin(tmp_path, MemoryRepositoryRegistry({"repo-a": tmp_path}))

    admin.save_repositories("default", ("repo-a",))

    raw = json.loads((tmp_path / "default.json").read_text())
    assert raw["start"] == "plan"
    assert raw["transitions"] == DEFINITION["transitions"]


def test_save_repositories_rejects_empty_and_leaves_file_untouched(tmp_path):
    _write(tmp_path, "default", {"repositories": ["repo-a"]})
    registry = MemoryRepositoryRegistry({"repo-a": tmp_path})
    admin = FilesystemProcessAdmin(tmp_path, registry)
    before = (tmp_path / "default.json").read_text()

    with pytest.raises(ProcessValidationError, match="no repositories selected"):
        admin.save_repositories("default", ())

    assert (tmp_path / "default.json").read_text() == before


def test_save_repositories_rejects_unknown_repository(tmp_path):
    _write(tmp_path, "default")
    admin = FilesystemProcessAdmin(tmp_path, MemoryRepositoryRegistry({"repo-a": tmp_path}))

    with pytest.raises(ProcessValidationError, match="repo-z"):
        admin.save_repositories("default", ("repo-a", "repo-z"))


def test_save_repositories_missing_process_raises(tmp_path):
    admin = FilesystemProcessAdmin(tmp_path, _registry())

    with pytest.raises(WorkflowNotFound):
        admin.save_repositories("unknown", "*")


def test_process_name_with_path_separator_is_rejected(tmp_path):
    admin = FilesystemProcessAdmin(tmp_path, _registry())

    with pytest.raises(WorkflowNotFound):
        admin.load_process("../secret")
    with pytest.raises(WorkflowNotFound):
        admin.save_repositories("../secret", "*")
