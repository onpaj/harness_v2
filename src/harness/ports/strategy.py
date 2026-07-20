from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Task


class EnqueueStrategy(ABC):
    """Picks which task from the queue comes up next."""

    @abstractmethod
    def select(self, tasks: list[Task]) -> Task | None:
        """The selected task, or None when there is nothing to pick from."""
