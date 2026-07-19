from __future__ import annotations

from abc import ABC, abstractmethod


class Clock(ABC):
    """Čas za portem, aby testy nemusely spát."""

    @abstractmethod
    def now(self) -> str:
        """Aktuální čas jako ISO 8601 UTC se sufixem Z."""

    @abstractmethod
    async def sleep(self, seconds: float) -> None:
        """Počkej."""
