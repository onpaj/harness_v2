"""Process definitions — a process is a named `Workflow` plus a repository
scope. `ProcessRepository` is the strict, build-time read side (used wherever
a process is actually consumed); `ProcessAdmin` is the write side behind the
editor, mirroring the existing `TaskControl`/`BoardView` split.

Load is lenient, save is strict: `ProcessAdmin.load_process` must not raise on
an already-invalid stored scope (the process's since-deregistered repo), or
the one UI meant to fix that drift could never open it. `ProcessRepository.
compile_process` and `ProcessAdmin.save_repositories` both validate strictly,
so a save always leaves the file in a state `compile_process` can load.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Process, ProcessSummary, RepositoryScope


class ProcessValidationError(Exception):
    """`repositories` is empty, or names a repository not in the registry."""


class ProcessRepository(ABC):
    @abstractmethod
    def compile_process(self, name: str) -> Process:
        """Load and fully validate a process. Raises `WorkflowNotFound` for a
        missing/broken definition file, `ProcessValidationError` when
        `repositories` is empty or names an unknown repository."""


class ProcessAdmin(ABC):
    """Operator-driven process editing, exposed to the UI behind a port."""

    @abstractmethod
    def list_processes(self) -> list[str]:
        """All process names, i.e. every `<name>.json` under the process root."""

    @abstractmethod
    def load_process(self, name: str) -> ProcessSummary:
        """Lenient read for the edit form. Does not raise
        `ProcessValidationError` even when the stored scope is currently
        invalid — that value must still be shown so the operator can fix it.
        Raises `WorkflowNotFound` if the process doesn't exist."""

    @abstractmethod
    def save_repositories(self, name: str, repositories: RepositoryScope) -> None:
        """Validate `repositories` (same rule as `compile_process`), then
        persist only that key — every other key in the file is passed through
        unchanged. Raises `WorkflowNotFound` if the process doesn't exist,
        `ProcessValidationError` (leaving the file untouched) if invalid."""
