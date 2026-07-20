"""The port the UI looks through.

All the API knows about the harness. Today's driver is an in-memory projection,
tomorrow it could be a read model in a database — the API won't notice.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from harness.models import Task

TODO_COLUMN = "todo"
"""Column for freshly loaded inbox tasks that have not started yet (status=None)."""

DONE_COLUMN = "done"
"""Column for tasks that reached END."""

FAILED_COLUMN = "failed"
"""Column for tasks that cannot be routed."""


@dataclass(frozen=True)
class BoardColumn:
    name: str
    tasks: tuple[Task, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tasks": [task.to_dict() for task in self.tasks]}


@dataclass(frozen=True)
class Board:
    revision: int
    columns: tuple[BoardColumn, ...]

    def column(self, name: str) -> BoardColumn | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "columns": [column.to_dict() for column in self.columns],
        }


class BoardView(ABC):
    """Read-only view of the harness state."""

    @abstractmethod
    def snapshot(self) -> Board:
        """The current board."""

    @abstractmethod
    def get(self, task_id: str) -> Task | None:
        """Task by id, or None."""

    @abstractmethod
    def subscribe(self) -> AsyncIterator[int]:
        """A stream of revision numbers. The first value is the current revision."""
