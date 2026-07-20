"""In-memory read model of live stage output — the stage-output twin of `BoardProjection`.

Fed from the same event stream: it's an `EventSink` handed into the composite in
`app.py`, and a `StageOutputView` the API reads through. It keeps a bounded ring
buffer of rendered lines per task and fans each new line out to whoever is
subscribed (the SSE endpoint), exactly the way `BoardProjection` fans out
revisions.

Scope is deliberately **live only**: a new stage (`claimed`) clears the buffer,
and the stream ends when the stage does (`consumed`/`failed`). Nothing is
persisted — the audit trail lives in the committed artifacts, not here.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from harness.ports.events import EventSink
from harness.ports.logs import StageOutputView

MAX_LINES = 2000
"""Ring-buffer depth per task. Old lines fall off — this is a live tail, not a log."""

_END = object()
"""Sentinel pushed to subscribers when a stage ends, so their stream completes."""


class StageOutputProjection(EventSink, StageOutputView):
    def __init__(self, max_lines: int = MAX_LINES) -> None:
        self._max_lines = max_lines
        self._buffers: dict[str, deque[str]] = {}
        self._subscribers: dict[str, set[asyncio.Queue[Any]]] = {}
        self._running: set[str] = set()

    def emit(self, name: str, **fields: Any) -> None:
        task_id = fields.get("task_id")
        if not isinstance(task_id, str):
            return
        if name == "claimed":
            self._reset(task_id)
        elif name == "stage_output":
            line = fields.get("line")
            if isinstance(line, str):
                self._append(task_id, line)
        elif name in ("consumed", "failed"):
            self._close(task_id)

    def tail(self, task_id: str) -> tuple[str, ...]:
        return tuple(self._buffers.get(task_id, ()))

    async def subscribe(self, task_id: str) -> AsyncIterator[str]:
        """Replay the buffer, then stream live lines until the stage ends.

        The queue is registered before the buffer snapshot is taken (both under
        the same synchronous run, no await between), so no line is missed or
        duplicated across the replay/live boundary.
        """
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._max_lines)
        self._subscribers.setdefault(task_id, set()).add(queue)
        try:
            for line in self.tail(task_id):
                yield line
            # Re-check *after* the replay (each yield above cedes control, during
            # which the stage may have ended): if it isn't running, there's
            # nothing live to wait for — end the stream instead of hanging.
            if task_id not in self._running:
                return
            while True:
                item = await queue.get()
                if item is _END:
                    return
                yield item
        finally:
            subscribers = self._subscribers.get(task_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(task_id, None)

    def _reset(self, task_id: str) -> None:
        self._buffers[task_id] = deque(maxlen=self._max_lines)
        self._running.add(task_id)

    def _append(self, task_id: str, line: str) -> None:
        buffer = self._buffers.get(task_id)
        if buffer is None:
            buffer = self._buffers[task_id] = deque(maxlen=self._max_lines)
        buffer.append(line)
        for queue in self._subscribers.get(task_id, ()):
            _push(queue, line)

    def _close(self, task_id: str) -> None:
        # The stage ended: end open streams and drop the buffer. Live-only scope
        # (output vanishes when the stage ends) — and it keeps `_buffers` bounded
        # to in-flight tasks rather than every task the process has ever seen.
        self._running.discard(task_id)
        self._buffers.pop(task_id, None)
        for queue in self._subscribers.get(task_id, ()):
            _push(queue, _END)


def _push(queue: asyncio.Queue[Any], item: Any) -> None:
    """Enqueue, dropping the oldest line when a slow subscriber's queue is full.

    A viewer that can't keep up loses old lines, never blocks the orchestration
    loop — the live tail is best-effort, the committed artifacts are the record.
    """
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            pass
