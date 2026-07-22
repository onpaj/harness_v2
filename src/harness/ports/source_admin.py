"""SourceAdmin — the write-side counterpart of the task sources, for the admin
UI. It is the third admin port alongside `AgentAdmin`/`WorkflowAdmin`: where
those manage `agents/*.json` and `workflows/*.json`, this one manages
`sources/*.json` — the data declaration of a `TaskSource` (today a
`GithubTaskSource`: which repo to scan, under which label, into which workflow
or step). Like `WorkflowAdmin`, sources stay raw text end to end — a round trip
through the editor is byte-identical unless the operator actually changed
something.

`SourceAdmin` is a UI-facing admin port, not an orchestration port — like
`BoardView`/`TaskControl`, not like `TaskSource` itself. The dispatcher and
consumer never touch it (invariant 33's shape).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SourceNotFound(Exception):
    """No source is defined under that name."""


class SourceValidationError(Exception):
    """A single message describing the first structural failure in a source
    definition — mirrors the exception `FilesystemSourceRepository.build` would
    raise, so the operator sees the same text the harness itself would log."""


class SourceAdmin(ABC):
    """Read/write access to task-source definition files, for the admin UI."""

    @abstractmethod
    def list(self) -> tuple[str, ...]:
        """Every source name currently defined, sorted."""

    @abstractmethod
    def read_raw(self, name: str) -> str:
        """Raises SourceNotFound. Returns the file's exact text."""

    @abstractmethod
    def write_raw(self, name: str, raw_json: str) -> None:
        """Parses raw_json through the same structural rules
        `FilesystemSourceRepository` applies (valid JSON object, a known `kind`,
        a non-empty `repository`, a `target` of exactly one of
        `{"workflow"}`/`{"step"}`) and writes the file verbatim (not
        re-serialized) only if it parses. Raises SourceValidationError — never
        partially writes."""

    @abstractmethod
    def delete(self, name: str) -> bool:
        """True if a source by that name existed and was removed."""
