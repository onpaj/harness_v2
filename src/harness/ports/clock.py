from __future__ import annotations

from abc import ABC, abstractmethod


class Clock(ABC):
    """Time behind a port, so tests don't have to sleep."""

    @abstractmethod
    def now(self) -> str:
        """The current time as ISO 8601 UTC with a Z suffix."""

    @abstractmethod
    async def sleep(self, seconds: float) -> None:
        """Wait."""
