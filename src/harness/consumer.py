"""Consumer: tenká obálka kolem ConsumerBehavior.

Nemá žádnou větev závislou na hodnotě outcome — jen ho doručí. Objeví-li se
tu `if outcome == ...`, prosákla odpovědnost přes hranici.
"""

from __future__ import annotations

from dataclasses import replace

from harness.ids import new_lock_id
from harness.models import FAILED, HistoryEntry, Outcome, Task, append_history
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue
from harness.ports.strategy import EnqueueStrategy


class Consumer:
    def __init__(
        self,
        *,
        step: str,
        queue: TaskQueue,
        inbox: TaskQueue,
        failed: TaskQueue,
        behavior: ConsumerBehavior,
        strategy: EnqueueStrategy,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._step = step
        self._queue = queue
        self._inbox = inbox
        self._failed = failed
        self._behavior = behavior
        self._strategy = strategy
        self._events = events
        self._clock = clock

    @property
    def actor(self) -> str:
        return f"consumer:{self._step}"

    async def tick(self) -> bool:
        """Zpracuj nejvýš jeden task. True, když se něco zpracovalo."""
        selected = self._strategy.select(self._queue.list())
        if selected is None:
            return False

        task = self._queue.claim(selected, new_lock_id())
        if task is None:
            return False

        try:
            outcome = await self._behavior.run(task)
        except Exception as error:  # noqa: BLE001 - jeden vadný task nesmí zastavit smyčku
            self._fail(task, f"behavior vyhodil výjimku: {error}")
            return True

        if not isinstance(outcome, Outcome):
            self._fail(task, f"behavior vrátil neplatný outcome: {outcome!r}")
            return True

        self._deliver(task, outcome)
        return True

    def _deliver(self, task: Task, outcome: Outcome) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=self.actor,
            from_step=self._step,
            to_step=None,
            outcome=outcome.value,
        )
        updated = append_history(
            replace(task, last_outcome=outcome.value, lock_id=None), entry
        )
        self._queue.transfer(updated, self._inbox)
        self._events.emit(
            "consumed", task_id=task.id, step=self._step, outcome=outcome.value
        )

    def _fail(self, task: Task, reason: str) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=self.actor,
            from_step=self._step,
            to_step=FAILED,
            reason=reason,
        )
        broken = append_history(
            replace(task, status=FAILED, lock_id=None), entry
        )
        self._queue.transfer(broken, self._failed)
        self._events.emit("failed", task_id=task.id, reason=reason)
