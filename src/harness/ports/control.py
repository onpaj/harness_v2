"""Operator control over tasks — the write-side counterpart of `BoardView`.

The board reads through `BoardView`; an operator acts through `TaskControl`.
Its verbs are `restart` (a failed task back to the inbox, from the start) and
`resume` (a terminal failure back into the step it died at). Neither is a
routing decision — both set state and let the dispatcher place the task.
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

    @abstractmethod
    def resume(self, task_id: str) -> bool:
        """Return a task whose terminal position was reached by failing to the
        inbox, rewound to the hop *before* the step it failed at, so the
        dispatcher routes it back into that step with its worktree and prior
        artifacts intact.

        Accepts a healer-retired task in `done/` (ADR-0024) and one still in
        `failed/`. True when a task by that id was found and requeued, False
        otherwise (unknown id, an ordinary completion, a failure with no step
        to return to, or a lost race).
        """

    @abstractmethod
    def delete(self, task_id: str) -> bool:
        """Permanently remove a task, wherever it currently sits unclaimed.
        True when a task by that id was found and removed, False otherwise
        (unknown id, already deleted, or currently claimed/in-flight).
        """
