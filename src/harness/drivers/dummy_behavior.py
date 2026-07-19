"""Dummy behavior fáze 1: počká a vrátí DONE.

Vrací DONE deterministicky. Volitelně pro jeden krok vrátí při prvním
průchodu REQUEST_CHANGES, aby se zpětná hrana workflow taky proklepla —
ale jen jednou, jinak by se smyčka točila donekonečna.
"""

from __future__ import annotations

from harness.models import Outcome, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock


class DummyBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        clock: Clock,
        delay: float = 5.0,
        request_changes_once_at: str | None = None,
    ) -> None:
        self._clock = clock
        self._delay = delay
        self._step = request_changes_once_at
        self._already_asked: set[str] = set()

    async def run(self, task: Task) -> Outcome:
        await self._clock.sleep(self._delay)

        if self._step is not None and task.status == self._step:
            if task.id not in self._already_asked:
                self._already_asked.add(task.id)
                return Outcome.REQUEST_CHANGES

        return Outcome.DONE
