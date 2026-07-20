"""FIFO by created. Ties are broken by id so the selection is deterministic."""

from __future__ import annotations

from harness.models import Task
from harness.ports.strategy import EnqueueStrategy


class FifoStrategy(EnqueueStrategy):
    def select(self, tasks: list[Task]) -> Task | None:
        if not tasks:
            return None
        return min(tasks, key=lambda task: (task.created, task.id))
