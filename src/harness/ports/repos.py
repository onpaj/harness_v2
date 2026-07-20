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
