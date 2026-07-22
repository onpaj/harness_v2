"""WorkflowAdmin — the write-side counterpart of `WorkflowRepository`, for the
admin UI. Workflows stay raw text end to end (never parsed and
re-serialized) — a round trip through the editor is byte-identical unless the
operator actually changed something.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class WorkflowValidationError(Exception):
    """A single message describing the first parse/schema failure — mirrors
    the exception `FilesystemWorkflowRepository.get` already raises today, so
    the operator sees the same text the harness itself would log."""


class WorkflowAdmin(ABC):
    """Read/write access to workflow definition files, for the admin UI."""

    @abstractmethod
    def list(self) -> tuple[str, ...]:
        """Every workflow name currently defined, sorted."""

    @abstractmethod
    def read_raw(self, name: str) -> str:
        """Raises WorkflowNotFound (reused from ports/workflows.py). Returns
        the file's exact text."""

    @abstractmethod
    def write_raw(self, name: str, raw_json: str) -> None:
        """Parses raw_json through the same rules `FilesystemWorkflowRepository
        .get` applies (valid JSON, `start` present, every transition has
        from/on/to) and writes the file verbatim (not re-serialized) only if
        it parses. Raises WorkflowValidationError — never partially writes."""

    @abstractmethod
    def delete(self, name: str) -> bool:
        """True if a workflow by that name existed and was removed."""
