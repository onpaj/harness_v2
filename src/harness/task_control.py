"""TaskControlService: the core behind the `TaskControl` port.

An operator reset, not a routing decision. It moves a failed task back to the
inbox with its state cleared (`status=None`) — exactly the shape of a fresh
submit — and lets the dispatcher decide where it goes next (invariant #3). Knows
only ports and models, never a driver.
"""

from __future__ import annotations

from dataclasses import replace

from harness.ids import new_lock_id
from harness.models import FAILED, HistoryEntry, append_history, resumable_failure
from harness.ports.board import TODO_COLUMN
from harness.ports.clock import Clock
from harness.ports.control import TaskControl
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue

ACTOR = "operator"


class TaskControlService(TaskControl):
    def __init__(
        self,
        *,
        inbox: TaskQueue,
        step_queues: dict[str, TaskQueue],
        done: TaskQueue,
        failed: TaskQueue,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._inbox = inbox
        self._step_queues = step_queues
        self._done = done
        self._failed = failed
        self._events = events
        self._clock = clock

    def restart(self, task_id: str) -> bool:
        found = next(
            (task for task in self._failed.list() if task.id == task_id), None
        )
        if found is None:
            return False

        claimed = self._failed.claim(found, new_lock_id())
        if claimed is None:
            # Lost the race — another actor moved the file first.
            return False

        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=FAILED,
            to_step=None,
            reason="restarted by operator",
        )
        reset = append_history(
            replace(claimed, status=None, last_outcome=None, lock_id=None), entry
        )
        self._failed.transfer(reset, self._inbox)
        self._events.emit(
            "restarted",
            task_id=task_id,
            queue=TODO_COLUMN,
            task=reset.to_dict(),
        )
        return True

    def resume(self, task_id: str) -> bool:
        """Rewind a terminal failure to just before the step it died at.

        Deliberately not a placement: it writes the `(status, lastOutcome)` pair
        the task held before it was dispatched into the failing step and returns
        it to the *inbox*, so `route()` re-derives that step and the dispatcher
        moves it (invariant #3). Nothing else is cleared — the worktree and the
        artifacts the earlier steps produced are the whole point.
        """
        for queue in (self._done, self._failed):
            found = next((task for task in queue.list() if task.id == task_id), None)
            if found is None:
                continue

            trace = resumable_failure(found)
            if trace is None:
                return False

            claimed = queue.claim(found, new_lock_id())
            if claimed is None:
                # Lost the race — another actor moved the file first.
                return False

            entry = HistoryEntry(
                at=self._clock.now(),
                actor=ACTOR,
                from_step=claimed.status,
                to_step=None,
                reason=f"resumed at {trace.failed_step!r} by operator",
            )
            reset = append_history(
                replace(
                    claimed,
                    status=trace.resume_status,
                    last_outcome=trace.resume_outcome,
                    lock_id=None,
                ),
                entry,
            )
            queue.transfer(reset, self._inbox)
            self._events.emit(
                "resumed",
                task_id=task_id,
                queue=TODO_COLUMN,
                task=reset.to_dict(),
            )
            return True
        return False

    def delete(self, task_id: str) -> bool:
        for queue in (self._inbox, *self._step_queues.values(), self._done, self._failed):
            found = next((task for task in queue.list() if task.id == task_id), None)
            if found is None:
                continue

            claimed = queue.claim(found, new_lock_id())
            if claimed is None:
                # Lost the race — another actor moved the file first.
                return False

            queue.discard(claimed)
            self._events.emit("deleted", task_id=task_id)
            return True
        return False
