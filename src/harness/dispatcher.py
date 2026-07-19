"""Dispatcher: rozhoduje, KAM task jde. Nikdy ne, co se s ním stalo."""

from __future__ import annotations

from dataclasses import replace

from harness.ids import new_lock_id
from harness.models import (
    Failed,
    Finished,
    HistoryEntry,
    MoveTo,
    Task,
    append_history,
)
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue
from harness.ports.strategy import EnqueueStrategy
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository
from harness.router import route

ACTOR = "dispatcher"


class Dispatcher:
    def __init__(
        self,
        *,
        inbox: TaskQueue,
        step_queues: dict[str, TaskQueue],
        done: TaskQueue,
        failed: TaskQueue,
        workflows: WorkflowRepository,
        strategy: EnqueueStrategy,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._inbox = inbox
        self._step_queues = step_queues
        self._done = done
        self._failed = failed
        self._workflows = workflows
        self._strategy = strategy
        self._events = events
        self._clock = clock

    def tick(self) -> bool:
        """Zpracuj nejvýš jeden task. True, když se něco zpracovalo."""
        selected = self._strategy.select(self._inbox.list())
        if selected is None:
            return False

        task = self._inbox.claim(selected, new_lock_id())
        if task is None:
            return False

        try:
            workflow = self._workflows.get(task.workflow_template)
        except WorkflowNotFound as error:
            self._fail(task, str(error))
            return True

        decision = route(task, workflow)

        if isinstance(decision, Failed):
            self._fail(task, decision.reason)
        elif isinstance(decision, Finished):
            self._finish(task)
        elif isinstance(decision, MoveTo):
            destination = self._step_queues.get(decision.step)
            if destination is None:
                self._fail(task, f"krok {decision.step!r} nemá frontu")
            else:
                self._move(task, decision.step, destination)

        return True

    def _move(self, task: Task, step: str, destination: TaskQueue) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=task.status,
            to_step=step,
            outcome=task.last_outcome,
        )
        routed = append_history(replace(task, status=step, lock_id=None), entry)
        self._inbox.transfer(routed, destination)
        self._events.emit(
            "dispatched",
            task_id=task.id,
            **{"from": task.status, "to": step},
            outcome=task.last_outcome,
        )

    def _finish(self, task: Task) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=task.status,
            to_step="end",
            outcome=task.last_outcome,
        )
        finished = append_history(replace(task, status="end", lock_id=None), entry)
        self._inbox.transfer(finished, self._done)
        self._events.emit("finished", task_id=task.id)

    def _fail(self, task: Task, reason: str) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=task.status,
            to_step="failed",
            outcome=task.last_outcome,
            reason=reason,
        )
        broken = append_history(replace(task, lock_id=None), entry)
        self._inbox.transfer(broken, self._failed)
        self._events.emit("failed", task_id=task.id, reason=reason)
