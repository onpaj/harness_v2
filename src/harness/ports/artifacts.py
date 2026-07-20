"""Artifact store port.

A harness-owned directory where phases write artifacts (plan, design, review).
Unversioned during the task, readable by the UI. Artifacts are attempt-indexed —
re-running a step never overwrites a previous attempt.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ArtifactRef:
    """The address of a single artifact: step, attempt, name."""

    step: str
    attempt: int
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step, "attempt": self.attempt, "name": self.name}


class ArtifactSlot(ABC):
    """A single attempt of a step. Everything written here belongs to one run."""

    @property
    @abstractmethod
    def attempt(self) -> int:
        """Attempt sequence number (0, 1, 2, …)."""

    @abstractmethod
    def put(self, name: str, content: str) -> None:
        """Write an artifact into this attempt."""


class ArtifactView(ABC):
    """Read-only view of artifacts. All the UI knows about the store."""

    @abstractmethod
    def list(self, task_id: str) -> tuple[ArtifactRef, ...]:
        """All of a task's artifacts across steps and attempts."""

    @abstractmethod
    def read(self, task_id: str, step: str, attempt: int, name: str) -> str | None:
        """The artifact's content, or None if it does not exist."""


class ArtifactStore(ArtifactView):
    """The write side of the store."""

    @abstractmethod
    def begin(self, task_id: str, step: str) -> ArtifactSlot:
        """Allocate the next attempt for the (task, step) pair and return a slot."""
