from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EventSink(ABC):
    """Structured output.

    Receives a name and fields, never a formatted string — formatting is the
    driver's job. Otherwise a future OTel driver would be parsing text.
    """

    @abstractmethod
    def emit(self, name: str, **fields: Any) -> None:
        """Emit an event."""
