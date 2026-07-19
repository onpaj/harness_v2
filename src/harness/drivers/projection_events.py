"""Most mezi proudem eventů a read modelem boardu."""

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
        column = fields.get("queue")
        if not isinstance(raw, dict) or not isinstance(column, str):
            return

        try:
            task = Task.from_dict(raw)
        except (KeyError, TypeError):
            return

        self._projection.apply(column, task)
