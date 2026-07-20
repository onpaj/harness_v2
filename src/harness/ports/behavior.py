from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import BehaviorResult, Task


class ConsumerBehavior(ABC):
    """The only place an outcome is produced.

    How the decision is reached is an implementation detail — today a sleep,
    in later phases a real agent. From the outside it makes no difference.
    """

    @abstractmethod
    async def run(self, task: Task) -> BehaviorResult:
        """Do the work and return what happened and what was done."""
