from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import BehaviorResult, Task


class ConsumerBehavior(ABC):
    """Jediné místo, kde vzniká outcome.

    Jak k rozhodnutí dojde, je vnitřní věc implementace — dnes sleep,
    v dalších fázích skutečný agent. Zvenčí se to neliší.
    """

    @abstractmethod
    async def run(self, task: Task) -> BehaviorResult:
        """Vykonej práci a vrať, co se stalo a co se udělalo."""
