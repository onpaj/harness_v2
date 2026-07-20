"""The port the UI looks through for a task's live stage output.

A second read surface next to `BoardView`/`ArtifactView`: the board shows *where*
a task is, the artifacts show *what it produced*, this shows *what the agent is
doing right now*. Read-only and driver-agnostic — today an in-memory ring buffer,
tomorrow whatever tails a log.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class StageOutputView(ABC):
    """Read-only view of the output the running stage produces, line by line."""

    @abstractmethod
    def tail(self, task_id: str) -> tuple[str, ...]:
        """The lines buffered for the task so far (empty if none / unknown)."""

    @abstractmethod
    def subscribe(self, task_id: str) -> AsyncIterator[str]:
        """Stream of lines for one task.

        Replays the currently-buffered lines first, then yields new ones as they
        arrive, and completes when the stage ends — so a caller can close cleanly.
        """
