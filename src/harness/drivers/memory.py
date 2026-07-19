"""In-memory drivery. Používají se v testech — bez disku a bez čekání."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from harness.models import Outcome, Task, Workflow
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository


class MemoryTaskQueue(TaskQueue):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._ready: dict[str, Task] = {}
        self._claimed: dict[str, Task] = {}

    def list(self) -> list[Task]:
        return list(self._ready.values())

    def claim(self, task: Task, lock_id: str) -> Task | None:
        if task.id not in self._ready:
            return None
        del self._ready[task.id]
        claimed = replace(task, lock_id=lock_id)
        self._claimed[task.id] = claimed
        return claimed

    def put(self, task: Task) -> None:
        self._ready[task.id] = task

    def transfer(self, task: Task, destination: TaskQueue) -> None:
        self._claimed.pop(task.id, None)
        destination.put(task)

    def recover(self) -> int:
        count = len(self._claimed)
        for task_id, task in self._claimed.items():
            self._ready[task_id] = replace(task, lock_id=None)
        self._claimed.clear()
        return count


class MemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, workflows: dict[str, Workflow]) -> None:
        self._workflows = workflows

    def get(self, name: str) -> Workflow:
        try:
            return self._workflows[name]
        except KeyError:
            raise WorkflowNotFound(f"workflow {name!r} neexistuje") from None


class MemoryEventSink(EventSink):
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, name: str, **fields: Any) -> None:
        self.events.append((name, fields))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


class FakeClock(Clock):
    def __init__(self, instant: str = "2026-07-19T10:00:00Z") -> None:
        self.instant = instant
        self.slept: list[float] = []

    def now(self) -> str:
        return self.instant

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


class ScriptedBehavior(ConsumerBehavior):
    """Vrací předepsané outcomes podle kroku, na kterém task stojí.

    Dojdou-li předpisy pro daný krok, vrací DONE.
    """

    def __init__(self, outcomes: dict[str, list[Outcome]] | None = None) -> None:
        self._outcomes = {step: list(values) for step, values in (outcomes or {}).items()}
        self.seen: list[str] = []

    async def run(self, task: Task) -> Outcome:
        step = task.status or ""
        self.seen.append(step)
        pending = self._outcomes.get(step)
        if pending:
            return pending.pop(0)
        return Outcome.DONE
