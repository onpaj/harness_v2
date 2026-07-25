from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class RepositoryNotFound(Exception):
    """No repo by that name is in the registry."""


class RepositoryRegistry(ABC):
    """A map from a repo's logical name → its root on disk.

    The map is machine-specific: only this registry knows where the repo lives
    on this machine. A task carries only the logical name (`"harness_v2"`),
    never a path — that keeps it portable across machines and stops a specific
    disk's layout from leaking into it."""

    @abstractmethod
    def resolve(self, name: str) -> Path:
        """Return the repo root for the name. Unknown name → RepositoryNotFound."""

    @abstractmethod
    def names(self) -> list[str]:
        """All repo names in the registry. A missing/unreadable registry yields
        an empty list — enumeration is lenient where `resolve` is strict."""

    def verify_command(self, name: str) -> str | None:
        """The repo's verify command (test suite), or None when it has none.

        Lenient like `names()`: an unknown name or an unreadable registry is
        None, never an exception — a repo without a verify command is a normal,
        supported state (the verify step no-ops on it).
        """
        return None
