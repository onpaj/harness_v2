from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EventSink(ABC):
    """Strukturovaný výstup.

    Dostává jméno a pole, nikdy naformátovaný řetězec — formátování je věc
    driveru. Jinak by budoucí OTel driver parsoval text.
    """

    @abstractmethod
    def emit(self, name: str, **fields: Any) -> None:
        """Vyemituj událost."""
