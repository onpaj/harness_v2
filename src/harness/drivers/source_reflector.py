"""Most mezi proudem harness eventů a projekcí do zdroje tasků.

Zrcadlí `ProjectionSink` (board), jen míří ven místo do UI. Mapuje harness
event na `Progress`/`FinishResult` bez znalosti GitHubu; adapter ten na label.
Routing podle `kind` řeší adapter (`_mine` guard), reflector volá všechny.
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
