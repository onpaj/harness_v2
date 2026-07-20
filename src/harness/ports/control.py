"""Operator control over tasks — the write-side counterpart of `BoardView`.

The board reads through `BoardView`; an operator acts through `TaskControl`.
Today its only verb is `restart` (a failed task back to the inbox); it is the
first step toward changing a task's stage. Like every port it knows no driver.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TaskControl(ABC):
    """Operator-driven task movement, exposed to the UI behind a port."""

    @abstractmethod
    def restart(self, task_id: str) -> bool:
        """Return a failed task to the inbox with its state reset, so the
        dispatcher re-routes it from the start. True when a task by that id was
        found in `failed/` and requeued, False otherwise (unknown id / lost race).
        """
