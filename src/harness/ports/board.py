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
"""Column for tasks that cannot be routed. With self-healing enabled it drains
as the healer processes each task into `healed`."""

HEALED_COLUMN = "healed"
"""Column for tasks the healer has settled — the never-consumed terminal that
takes over that role from `failed` once a healer is wired."""

UNKNOWN_WORKFLOW = "unknown"
"""Reserved tab for tasks whose workflow_template names no discovered
definition. Never a real workflow's name (workflow names come from filenames,
and "unknown" collides with nothing in practice)."""


@dataclass(frozen=True)
class BoardColumn:
    name: str
    tasks: tuple[Task, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tasks": [task.to_dict() for task in self.tasks]}


@dataclass(frozen=True)
class BoardTab:
    name: str
    columns: tuple[BoardColumn, ...]

    def column(self, name: str) -> BoardColumn | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "columns": [column.to_dict() for column in self.columns]}


@dataclass(frozen=True)
class Board:
    revision: int
    workflows: tuple[BoardTab, ...]

    def workflow(self, name: str) -> BoardTab | None:
        for tab in self.workflows:
            if tab.name == name:
                return tab
        return None

    def default_tab(self) -> str | None:
        """"default" if present, else the first tab alphabetically, else None
        (an empty board — no workflow definitions and no orphaned tasks)."""
        names = [tab.name for tab in self.workflows]
        if "default" in names:
            return "default"
        return names[0] if names else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "workflows": [tab.to_dict() for tab in self.workflows],
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
