"""Bridge between the harness event stream and the projection into the task source.

Mirrors `ProjectionSink` (board), only it points outward instead of to the UI. It
maps a harness event to `Progress`/`FinishResult` without GitHub knowledge; the
adapter turns that into a label. Routing by `kind` is the adapter's job (`_mine`
guard), the reflector calls all of them.
"""

from __future__ import annotations

from typing import Any

from harness.models import Task
from harness.ports.events import EventSink
from harness.ports.source import FinishResult, Progress, TaskSource


class SourceReflectorSink(EventSink):
    def __init__(self, sources: list[TaskSource]) -> None:
        self._sources = sources

    def emit(self, name: str, **fields: Any) -> None:
        raw = fields.get("task")
        if not isinstance(raw, dict):
            return
        try:
            task = Task.from_dict(raw)
        except (KeyError, TypeError):
            return

        if name == "dispatched":
            step = fields.get("to") or fields.get("queue", "")
            progress = Progress(step=step, summary="")
            for source in self._sources:
                source.report_progress(task, progress)
        elif name == "finished":
            for source in self._sources:
                source.finish(task, FinishResult(ok=True))
        elif name == "failed":
            result = FinishResult(ok=False, summary=fields.get("reason", ""))
            for source in self._sources:
                source.finish(task, result)
