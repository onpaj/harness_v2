"""Rozbočka event sinků.

Dispatcher a consumer emitují do jediného portu a nevědí, kolik posluchačů
za ním stojí.
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
            except Exception:  # noqa: BLE001 - selhání pozorovatele nesmí zastavit smyčku
                # Hlášení na stderr — na EventSink se hlásit nemůžeme, ten právě selhal.
                # Orchestrační smyčka musí pokračovat, ale porucha nesmí zmizet beze stopy.
                traceback.print_exc()
