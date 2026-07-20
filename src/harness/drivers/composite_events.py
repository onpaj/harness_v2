"""Fan-out for event sinks.

The dispatcher and consumer emit into a single port and do not know how many
listeners stand behind it.
"""

from __future__ import annotations

import traceback
from typing import Any

from harness.ports.events import EventSink


class CompositeEventSink(EventSink):
    def __init__(self, *sinks: EventSink) -> None:
        self._sinks = sinks

    def emit(self, name: str, **fields: Any) -> None:
        for sink in self._sinks:
            try:
                sink.emit(name, **fields)
            except Exception:  # noqa: BLE001 - an observer's failure must not stop the loop
                # Report to stderr — we cannot report to the EventSink, it just failed.
                # The orchestration loop must go on, but the fault must not vanish without a trace.
                traceback.print_exc()
