"""Consumer: a thin wrapper around ConsumerBehavior.

It has no branch that depends on the outcome value — it just delivers it. If an
`if outcome == ...` shows up here, responsibility has leaked across the boundary.
"""

from __future__ import annotations

from dataclasses import replace

from harness.ids import new_lock_id
from harness.models import (
    FAILED,
    BehaviorResult,
    HistoryEntry,
    Outcome,
    Task,
    append_history,
)
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
    def step(self) -> str:
        return self._step

    @property
    def actor(self) -> str:
        return f"consumer:{self._step}"

    async def tick(self) -> bool:
        """Process at most one task. True when something was processed."""
        selected = self._strategy.select(self._queue.list())
        if selected is None:
            return False

        task = self._queue.claim(selected, new_lock_id())
        if task is None:
            return False

        self._events.emit(
            "claimed",
            task_id=task.id,
            step=self._step,
            queue=self._step,
            task=task.to_dict(),
        )

        try:
            result = await self._behavior.run(task)
        except Exception as error:  # noqa: BLE001 - one bad task must not stop the loop
            self._fail(task, f"behavior raised an exception: {error}")
            return True

        if not isinstance(result, BehaviorResult) or not isinstance(
            result.outcome, Outcome
        ):
            self._fail(task, f"behavior returned an invalid result: {result!r}")
            return True

        self._deliver(task, result)
        return True

    def _deliver(self, task: Task, result: BehaviorResult) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=self.actor,
            from_step=self._step,
            to_step=None,
            outcome=result.outcome.value,
            summary=result.summary or None,
        )
        updated = append_history(
            replace(task, last_outcome=result.outcome.value, lock_id=None), entry
        )
        self._queue.transfer(updated, self._inbox)
        self._events.emit(
            "consumed",
            task_id=task.id,
            step=self._step,
            outcome=result.outcome.value,
            summary=result.summary,
            queue=self._step,
            task=updated.to_dict(),
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
        self._events.emit(
            "failed",
            task_id=task.id,
            reason=reason,
            queue="failed",
            task=broken.to_dict(),
        )
