from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Task


class EnqueueStrategy(ABC):
    """Vybírá, který task z fronty přijde na řadu."""

    @abstractmethod
    def select(self, tasks: list[Task]) -> Task | None:
        """Vybraný task, nebo None když není z čeho vybírat."""
