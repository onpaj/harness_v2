"""`FilesystemSourceRepository` / `FilesystemSourceAdmin`: `sources/*.json`
→ `GithubTaskSource`s, and the write-side admin over the same files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.drivers.fs_sources import (
    FilesystemSourceAdmin,
    FilesystemSourceRepository,
)
from harness.drivers.github_client import FakeGithubClient, Issue
from harness.drivers.memory import FakeClock
from harness.drivers.github_source import GithubTaskSource
from harness.ports.repos import RepositoryRegistry, RepositoryNotFound
from harness.ports.source_admin import SourceNotFound, SourceValidationError


class FakeRegistry(RepositoryRegistry):
    """name → path map for tests; the path's git origin is faked via slug_of."""

    def __init__(self, names: dict[str, str]) -> None:
        self._names = {name: Path(path) for name, path in names.items()}

    def resolve(self, name: str) -> Path:
        if name not in self._names:
            raise RepositoryNotFound(name)
        return self._names[name]

    def names(self) -> list[str]:
        return list(self._names)


def _write(root: Path, name: str, body: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


def _slug_of(path: Path):
    # Map a repo path to a slug for tests, or None to simulate "no GitHub origin".
    return None if path.name == "no-origin" else f"onpaj/{path.name}"


# --- FilesystemSourceRepository.build ---------------------------------------


def test_valid_github_workflow_file_builds_one_source(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "harness-issues",
        {
            "kind": "github",
            "repository": "harness_v2",
            "select_label": "harness:todo",
            "target": {"workflow": "default"},
        },
    )
    registry = FakeRegistry({"harness_v2": "/repos/harness_v2"})

    sources = FilesystemSourceRepository(tmp_path).build(
        clock=FakeClock(),
        client=FakeGithubClient(),
        registry=registry,
        worktree_root="/wt",
        slug_of=_slug_of,
    )

    assert len(sources) == 1
    (source,) = sources
    assert isinstance(source, GithubTaskSource)
    assert source._repo == "onpaj/harness_v2"
    assert source._repository == "harness_v2"
    assert source._workflow == "default"
    assert source._step is None
    assert source._select_label == "harness:todo"


def test_step_target_builds_a_workflow_less_source(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "triage",
        {"kind": "github", "repository": "harness_v2", "target": {"step": "review"}},
    )
    sources = FilesystemSourceRepository(tmp_path).build(
        clock=FakeClock(),
        client=FakeGithubClient(),
        registry=FakeRegistry({"harness_v2": "/repos/harness_v2"}),
        worktree_root="/wt",
        slug_of=_slug_of,
    )
    (source,) = sources
    assert source._step == "review"
    assert source._workflow is None
    # default select label applied
    assert source._select_label == "harness:todo"


def test_built_source_ingests_issues(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "harness-issues",
        {"kind": "github", "repository": "harness_v2", "target": {"workflow": "default"}},
    )
    client = FakeGithubClient([Issue(1, "Fix", "body", "u1", ("harness:todo",))])
    sources = FilesystemSourceRepository(tmp_path).build(
        clock=FakeClock(),
        client=client,
        registry=FakeRegistry({"harness_v2": "/repos/harness_v2"}),
        worktree_root="/wt",
        slug_of=_slug_of,
    )
    tasks = sources[0].poll()
    assert len(tasks) == 1
    assert tasks[0].data["title"] == "Fix"
    assert tasks[0].data["source"]["issue"] == 1


def test_missing_dir_or_no_client_yields_no_sources(tmp_path: Path) -> None:
    repo = FilesystemSourceRepository(tmp_path / "absent")
    assert repo.build(
        clock=FakeClock(), client=FakeGithubClient(),
        registry=FakeRegistry({}), worktree_root="/wt",
    ) == []
    _write(tmp_path, "s", {"kind": "github", "repository": "r", "target": {"workflow": "default"}})
    assert FilesystemSourceRepository(tmp_path).build(
        clock=FakeClock(), client=None,
        registry=FakeRegistry({"r": "/r"}), worktree_root="/wt", slug_of=_slug_of,
    ) == []


def test_unknown_repo_or_no_origin_is_skipped_not_fatal(tmp_path: Path) -> None:
    _write(tmp_path, "unknown", {"kind": "github", "repository": "ghost", "target": {"workflow": "default"}})
    _write(tmp_path, "noorigin", {"kind": "github", "repository": "local", "target": {"workflow": "default"}})
    sources = FilesystemSourceRepository(tmp_path).build(
        clock=FakeClock(),
        client=FakeGithubClient(),
        registry=FakeRegistry({"local": "/repos/no-origin"}),
        worktree_root="/wt",
        slug_of=_slug_of,
    )
    assert sources == []  # ghost unknown, local has no origin


def test_known_targets_rejects_unserved_target(tmp_path: Path) -> None:
    _write(tmp_path, "bad", {"kind": "github", "repository": "harness_v2", "target": {"workflow": "ghost"}})
    with pytest.raises(SourceValidationError):
        FilesystemSourceRepository(tmp_path).build(
            clock=FakeClock(),
            client=FakeGithubClient(),
            registry=FakeRegistry({"harness_v2": "/repos/harness_v2"}),
            worktree_root="/wt",
            known_targets={"default"},
            slug_of=_slug_of,
        )


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "gitlab", "repository": "r", "target": {"workflow": "w"}},
        {"kind": "github", "target": {"workflow": "w"}},
        {"kind": "github", "repository": "r"},
        {"kind": "github", "repository": "r", "target": {"workflow": "w", "step": "s"}},
        {"kind": "github", "repository": "r", "target": {}},
        {"kind": "github", "repository": "", "target": {"workflow": "w"}},
    ],
)
def test_invalid_file_raises_naming_the_file(tmp_path: Path, body: dict) -> None:
    _write(tmp_path, "broken", body)
    with pytest.raises(SourceValidationError) as excinfo:
        FilesystemSourceRepository(tmp_path).build(
            clock=FakeClock(),
            client=FakeGithubClient(),
            registry=FakeRegistry({"r": "/repos/r"}),
            worktree_root="/wt",
            slug_of=_slug_of,
        )
    assert "broken.json" in str(excinfo.value)


def test_repositories_lists_referenced_repo_names(tmp_path: Path) -> None:
    _write(tmp_path, "a", {"kind": "github", "repository": "repo-a", "target": {"workflow": "w"}})
    _write(tmp_path, "b", {"kind": "github", "repository": "repo-b", "target": {"step": "s"}})
    assert FilesystemSourceRepository(tmp_path).repositories() == {"repo-a", "repo-b"}
    assert FilesystemSourceRepository(tmp_path / "absent").repositories() == set()


# --- FilesystemSourceAdmin ---------------------------------------------------


def test_admin_list_read_write_delete_roundtrip(tmp_path: Path) -> None:
    admin = FilesystemSourceAdmin(tmp_path)
    assert admin.list() == ()

    raw = json.dumps(
        {"kind": "github", "repository": "harness_v2", "target": {"workflow": "default"}},
        indent=2,
    )
    admin.write_raw("harness-issues", raw)

    assert admin.list() == ("harness-issues",)
    assert admin.read_raw("harness-issues") == raw  # byte-identical
    assert admin.delete("harness-issues") is True
    assert admin.delete("harness-issues") is False
    assert admin.list() == ()


def test_admin_read_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceNotFound):
        FilesystemSourceAdmin(tmp_path).read_raw("ghost")


def test_admin_write_rejects_invalid_definition_without_writing(tmp_path: Path) -> None:
    admin = FilesystemSourceAdmin(tmp_path)
    with pytest.raises(SourceValidationError):
        admin.write_raw("bad", json.dumps({"kind": "github", "repository": "r"}))
    with pytest.raises(SourceValidationError):
        admin.write_raw("bad", "{not json")
    assert admin.list() == ()


def test_admin_rejects_unsafe_name(tmp_path: Path) -> None:
    admin = FilesystemSourceAdmin(tmp_path)
    with pytest.raises(SourceValidationError):
        admin.write_raw(
            "../escape",
            json.dumps({"kind": "github", "repository": "r", "target": {"workflow": "w"}}),
        )
