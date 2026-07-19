"""Port, kterým se dívá UI.

Jediné, co API o harnessu ví. Dnešní driver je in-memory projekce, zítra
může být read model v databázi — API se to nedotkne.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from harness.models import Task

DONE_COLUMN = "done"
"""Sloupec pro tasky, které doputovaly na END."""

FAILED_COLUMN = "failed"
"""Sloupec pro tasky, které nelze směrovat."""


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
    """Read-only pohled na stav harnessu."""

    @abstractmethod
    def snapshot(self) -> Board:
        """Aktuální board."""

    @abstractmethod
    def get(self, task_id: str) -> Task | None:
        """Task podle id, nebo None."""

    @abstractmethod
    def subscribe(self) -> AsyncIterator[int]:
        """Proud čísel revizí. První hodnota je aktuální revize."""
