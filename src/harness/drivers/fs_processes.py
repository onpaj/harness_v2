"""A process as <root>/<name>.json — the same file `FilesystemWorkflowRepository`
reads, with one additional optional key: `"repositories"`.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.drivers.fs_workflows import FilesystemWorkflowRepository, invalid_workflow_name
from harness.models import Process, ProcessSummary, RepositoryScope
from harness.ports.processes import ProcessAdmin, ProcessRepository, ProcessValidationError
from harness.ports.repos import RepositoryRegistry
from harness.ports.workflows import WorkflowNotFound


def _read_raw(path: Path, name: str) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise WorkflowNotFound(f"workflow {name!r} does not exist ({path})") from None
    except json.JSONDecodeError as error:
        raise WorkflowNotFound(
            f"workflow {name!r} has a broken definition: {error}"
        ) from None
    if not isinstance(raw, dict):
        raise WorkflowNotFound(
            f"workflow {name!r} has an invalid definition: expected object, "
            f"got {type(raw).__name__}"
        )
    return raw


def _parse_repositories(value: object, name: str) -> RepositoryScope:
    """Turn the raw JSON value of `"repositories"` into a `RepositoryScope`.
    Does not validate against the registry — that is `_validate_repositories`."""
    if value == "*":
        return "*"
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ProcessValidationError(
        f"process {name!r} has an invalid 'repositories' value: expected a "
        f'list of repository names or "*", got {value!r}'
    )


def _validate_repositories(
    repositories: RepositoryScope, name: str, registry: RepositoryRegistry
) -> None:
    if repositories == "*":
        return
    if len(repositories) == 0:
        raise ProcessValidationError(
            f"process {name!r} has no repositories selected — choose specific "
            "repositories or 'all'"
        )
    known = registry.names()
    unknown = sorted(repo for repo in repositories if repo not in known)
    if unknown:
        plural = "y" if len(unknown) == 1 else "ies"
        known_text = ", ".join(sorted(known)) if known else "(none registered)"
        raise ProcessValidationError(
            f"process {name!r} names unknown repositor{plural} "
            f"{', '.join(unknown)}; known repositories: {known_text}"
        )


class FilesystemProcessRepository(ProcessRepository):
    def __init__(self, root: Path, registry: RepositoryRegistry) -> None:
        self._root = Path(root)
        self._registry = registry

    def compile_process(self, name: str) -> Process:
        workflow = FilesystemWorkflowRepository(self._root).get(name)
        raw = _read_raw(self._root / f"{name}.json", name)
        repositories = _parse_repositories(raw.get("repositories", "*"), name)
        _validate_repositories(repositories, name, self._registry)
        return Process(name=name, workflow=workflow, repositories=repositories)


class FilesystemProcessAdmin(ProcessAdmin):
    def __init__(self, root: Path, registry: RepositoryRegistry) -> None:
        self._root = Path(root)
        self._registry = registry

    def list_processes(self) -> list[str]:
        return sorted(path.stem for path in self._root.glob("*.json"))

    def load_process(self, name: str) -> ProcessSummary:
        if invalid_workflow_name(name):
            raise WorkflowNotFound(f"invalid workflow name: {name!r}")
        workflow = FilesystemWorkflowRepository(self._root).get(name)
        raw = _read_raw(self._root / f"{name}.json", name)
        repositories = _parse_repositories(raw.get("repositories", "*"), name)
        return ProcessSummary(
            name=name, start=workflow.start, steps=workflow.steps(),
            repositories=repositories,
        )

    def save_repositories(self, name: str, repositories: RepositoryScope) -> None:
        if invalid_workflow_name(name):
            raise WorkflowNotFound(f"invalid workflow name: {name!r}")
        path = self._root / f"{name}.json"
        raw = _read_raw(path, name)
        _validate_repositories(repositories, name, self._registry)
        raw["repositories"] = list(repositories) if repositories != "*" else "*"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
