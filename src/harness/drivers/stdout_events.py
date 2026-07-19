"""Eventy jako řádky na stdout. V dalších fázích nahradí OTel."""

from __future__ import annotations

import sys
from typing import Any, TextIO

from harness.ports.events import EventSink


class StdoutEventSink(EventSink):
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def emit(self, name: str, **fields: Any) -> None:
        printable = {key: value for key, value in fields.items() if key != "task"}
        rendered = " ".join(f"{key}={value}" for key, value in printable.items())
        line = f"{name} {rendered}".rstrip()
        print(line, file=self._stream, flush=True)
