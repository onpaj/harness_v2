"""MergeReconciler: the core that pulls merge status back in and archives.

A structural twin of `SourcePoller`: `SourcePoller` pulls work *in* from the
outside world, `MergeReconciler` pulls the outcome of already-landed work back
*in* and retires it from the live board. The core stays GitHub-blind — it
knows only ports (`MergeChecker`, `TaskQueue`, `EventSink`, `Clock`), never a
driver.

Selection is least-recently-checked, not "first candidate every time": a task
whose PR is still open gets released back into `done` with a `checkedAt`
stamp, and the next tick picks whichever candidate has waited longest since
its last check. Without this, a single stubborn open PR would starve every
other candidate in `done` forever.
"""

from __future__ import annotations

from dataclasses import replace

from harness.ids import new_lock_id
from harness.models import HistoryEntry, Task, append_history
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.merge import MergeChecker
from harness.ports.queue import TaskQueue

ACTOR = "merge_reconciler"


class MergeReconciler:
    def __init__(
        self,
        *,
        done: TaskQueue,
        archived: TaskQueue,
        checker: MergeChecker,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._done = done
        self._archived = archived
        self._checker = checker
        self._events = events
        self._clock = clock

    def tick(self) -> bool:
        """Examine at most one candidate. True only when a task was archived.

        Every other outcome (no candidate, lost claim race, checker error,
        checked-but-still-open) returns False — a tick that merely examined a
        task without changing its state isn't "work done" in the sense the
        caller's backoff loop cares about. Returning True here would let the
        loop tight-spin across every open-PR task before ever backing off,
        defeating the point of a gentle reconcile interval.
        """
        candidates = [
            task for task in self._done.list() if isinstance(task.data.get("pr"), dict)
        ]
        if not candidates:
            return False

        candidates.sort(
            key=lambda task: (task.data["pr"].get("checkedAt") or "", task.created, task.id)
        )
        task = self._done.claim(candidates[0], new_lock_id())
        if task is None:
            return False

        try:
            merged = self._checker.is_merged(task)
        except Exception as error:  # noqa: BLE001 - a checker failure must not stop the loop
            self._release(task, checked_at=None)
            self._events.emit("merge_check_error", task_id=task.id, error=str(error))
            return False

        if not merged:
            self._release(task, checked_at=self._clock.now())
            return False

        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=task.status,
            to_step="archived",
            summary=f"PR {task.data['pr'].get('url', '')} merged",
        )
        updated = append_history(replace(task, lock_id=None), entry)
        self._done.transfer(updated, self._archived)
        self._events.emit(
            "archived", task_id=updated.id, queue="archived", task=updated.to_dict()
        )
        return True

    def _release(self, task: Task, *, checked_at: str | None) -> None:
        """Put a still-open or unchecked candidate back where it came from.

        Stamps `checkedAt` on a successful (non-raising) check so the next
        tick's least-recently-checked sort picks a different candidate — a
        checker error deliberately does *not* stamp it, so the failed task is
        retried before tasks that were successfully checked.
        """
        pr = dict(task.data["pr"])
        if checked_at is not None:
            pr["checkedAt"] = checked_at
        released = replace(task, lock_id=None, data={**task.data, "pr": pr})
        self._done.transfer(released, self._done)
        self._events.emit(
            "rechecked",
            task_id=released.id,
            queue=self._done.name,
            task=released.to_dict(),
        )
