"""Task sources as data: `<root>/<name>.json` → `TaskSource`s.

Symmetric to `FilesystemAgentCatalog`/`FilesystemWorkflowRepository`/
`FilesystemTriggerRepository`: a source is a small JSON file naming a `kind`
(`"github"` only, in v1), the `repository` it scans (a repo *name*, resolved to
a slug via the `RepositoryRegistry` — invariant #15), a `select_label` and a
`target` that is exactly one of `{"workflow": ...}` / `{"step": ...}`. This is
the data-driven way to declare the periodic GitHub-issue ingestion that used to
live only in `--github-*` CLI flags.

Two readers, mirroring the agents/workflows split:
- `FilesystemSourceRepository.build()` constructs the live `GithubTaskSource`s
  for a run (it needs a `GithubClient` and the registry to resolve name → slug).
- `FilesystemSourceAdmin` is the write-side for the admin UI: it lists, reads,
  validates-and-writes and deletes the raw files, needing neither a client nor
  the registry. Structural validation (`parse_source`) is shared, so the UI and
  the run reject the same malformed file with the same message.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from harness.drivers.git_remote import github_slug
from harness.drivers.github_client import GithubClient
from harness.drivers.github_source import GithubTaskSource
from harness.ports.clock import Clock
from harness.ports.repos import RepositoryRegistry
from harness.ports.source import TaskSource
from harness.ports.source_admin import (
    SourceAdmin,
    SourceNotFound,
    SourceValidationError,
)

SUPPORTED_KINDS = ("github",)
DEFAULT_SELECT_LABEL = "harness:todo"


@dataclass(frozen=True)
class SourceDefinition:
    """The validated, structural shape of a `sources/*.json` file. The `repository`
    is still a name here — the slug is resolved at build time (invariant #15)."""

    name: str
    kind: str
    repository: str
    select_label: str
    workflow: str | None
    step: str | None


def parse_source(raw: object, *, fallback_name: str) -> SourceDefinition:
    """Validate a raw source definition structurally, raising
    `SourceValidationError` on the first problem. Shared by the run-time
    repository and the admin so both reject the same file the same way. Does not
    resolve the repository name or consult served workflows — those are run-time
    concerns (a repo may not be cloned here; a workflow may not be served)."""
    if not isinstance(raw, dict):
        raise SourceValidationError(
            f"expected a JSON object, got {type(raw).__name__}"
        )

    kind = raw.get("kind")
    if kind not in SUPPORTED_KINDS:
        raise SourceValidationError(
            f"unknown kind {kind!r} (only {', '.join(map(repr, SUPPORTED_KINDS))} "
            f"supported)"
        )

    repository = raw.get("repository")
    if not isinstance(repository, str) or not repository.strip():
        raise SourceValidationError("repository is required and must be a non-empty string")

    select_label = raw.get("select_label", DEFAULT_SELECT_LABEL)
    if not isinstance(select_label, str) or not select_label.strip():
        raise SourceValidationError("select_label must be a non-empty string")

    workflow, step = _parse_target(raw.get("target"))

    name = raw.get("name", fallback_name)
    if not isinstance(name, str) or not name.strip():
        raise SourceValidationError("name must be a non-empty string")

    return SourceDefinition(
        name=name,
        kind=kind,
        repository=repository,
        select_label=select_label,
        workflow=workflow,
        step=step,
    )


def _parse_target(target: object) -> tuple[str | None, str | None]:
    if not isinstance(target, dict) or set(target) not in ({"workflow"}, {"step"}):
        raise SourceValidationError(
            "target must be exactly one of {'workflow': ...} / {'step': ...}"
        )
    workflow = target.get("workflow")
    step = target.get("step")
    value = workflow if workflow is not None else step
    if not isinstance(value, str) or not value.strip():
        raise SourceValidationError("target value must be a non-empty string")
    return workflow, step


class FilesystemSourceRepository:
    """Reads `<root>/*.json` and builds one live `TaskSource` per file."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def build(
        self,
        *,
        clock: Clock,
        client: GithubClient | None,
        registry: RepositoryRegistry,
        worktree_root: str,
        known_targets: set[str] | None = None,
        step_labels: dict[str, str] | None = None,
        slug_of=github_slug,
    ) -> list[TaskSource]:
        """Construct the sources declared on disk. Like the CLI-flag path, a
        missing/empty directory or absent `client` (no token) yields `[]`, so the
        harness runs on `harness submit` alone. A file's `repository` that names
        no known repo, or a repo with no GitHub origin, is skipped with a warning
        — not fatal, since a machine may not have every repo cloned."""
        if not self._root.exists() or client is None:
            return []

        known_names = set(registry.names())
        sources: list[TaskSource] = []
        for path in sorted(self._root.glob("*.json")):
            definition = self._parse_file(path, known_targets=known_targets)
            if definition.repository not in known_names:
                print(
                    f"warning: source {path.name} names unknown repository "
                    f"{definition.repository!r}, not scanned",
                    file=sys.stderr,
                )
                continue
            slug = slug_of(registry.resolve(definition.repository))
            if slug is None:
                print(
                    f"warning: source {path.name}'s repository "
                    f"{definition.repository!r} has no GitHub origin, not scanned",
                    file=sys.stderr,
                )
                continue
            sources.append(
                GithubTaskSource(
                    client=client,
                    clock=clock,
                    repo=slug,
                    workflow=definition.workflow,
                    step=definition.step,
                    repository=definition.repository,
                    worktree_root=worktree_root,
                    select_label=definition.select_label,
                    step_labels=step_labels,
                )
            )
        return sources

    def repositories(self) -> set[str]:
        """The repo names every declared source references — used by the CLI to
        decide whether the repos.json auto-scan should stand down."""
        if not self._root.exists():
            return set()
        names: set[str] = set()
        for path in sorted(self._root.glob("*.json")):
            names.add(self._parse_file(path, known_targets=None).repository)
        return names

    def _parse_file(
        self, path: Path, *, known_targets: set[str] | None
    ) -> SourceDefinition:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SourceValidationError(
                f"source {path.name} has a broken definition: {error}"
            ) from None
        try:
            definition = parse_source(raw, fallback_name=path.stem)
        except SourceValidationError as error:
            raise SourceValidationError(f"source {path.name}: {error}") from None
        if known_targets is not None:
            value = definition.workflow if definition.workflow else definition.step
            if value not in known_targets:
                raise SourceValidationError(
                    f"source {path.name} targets {value!r}, which is not a known "
                    f"workflow or step"
                )
        return definition


def _invalid_source_name(name: str) -> bool:
    return not name or "/" in name or "\\" in name or name.startswith(".")


class FilesystemSourceAdmin(SourceAdmin):
    """Write-side of `sources/*.json` for the admin UI. Validates structurally on
    write (never resolving the repo or consulting served workflows — those stay
    run-time concerns surfaced as soft warnings by the route) and writes the raw
    text verbatim, so a round trip is byte-identical."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def list(self) -> tuple[str, ...]:
        if not self._root.exists():
            return ()
        return tuple(sorted(path.stem for path in self._root.glob("*.json")))

    def read_raw(self, name: str) -> str:
        path = self._path(name)
        if not path.exists():
            raise SourceNotFound(f"source {name!r} does not exist")
        return path.read_text(encoding="utf-8")

    def write_raw(self, name: str, raw_json: str) -> None:
        if _invalid_source_name(name):
            raise SourceValidationError(f"invalid source name {name!r}")
        try:
            raw = json.loads(raw_json)
        except json.JSONDecodeError as error:
            raise SourceValidationError(f"broken JSON: {error}") from None
        parse_source(raw, fallback_name=name)  # validate only; raises on failure
        self._root.mkdir(parents=True, exist_ok=True)
        self._path(name).write_text(raw_json, encoding="utf-8")

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _path(self, name: str) -> Path:
        return self._root / f"{name}.json"
