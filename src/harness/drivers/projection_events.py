"""Bridge between the event stream and the board's read model."""

from __future__ import annotations

from typing import Any

from harness.models import Task
from harness.ports.events import EventSink
from harness.projection import BoardProjection


class ProjectionSink(EventSink):
    def __init__(self, projection: BoardProjection) -> None:
        self._projection = projection

    def emit(self, name: str, **fields: Any) -> None:
        raw = fields.get("task")
        if not isinstance(raw, dict):
            return

        try:
            task = Task.from_dict(raw)
        except (KeyError, TypeError):
            return

        if name == "archived":
            self._projection.archive(task)
            return

        column = fields.get("queue")
        if not isinstance(column, str):
            return
        self._projection.apply(column, task)
