"""RetentionReconciler: retires terminal tasks that settled long enough ago.

Nobody consumes `done` or `healed`, so a task that reaches one stays on the
board for the lifetime of the root. Recurring Processes settle several tasks a
day, and the board grows without bound — most visibly in the `No workflow` tab,
where step-targeted Processes land.

Which queues get swept is the caller's decision, not this module's, and `app.py`
deliberately leaves `failed/` out: a failure the harness declined to heal must
keep reading as a problem where the operator looks, and stay restartable
(ADR-0024 — `TaskControlService.restart` searches `failed/` and nowhere else).

This is the rule that says *when* a settled task should go. "Go" is the exact
`archived/` disposition `PrWatcher`, `MergeReconciler` and `IssueReconciler`
already share: off every board column, still gettable by id, file intact.
Nothing is deleted.

Age is measured from when the task **settled** — the last history entry, the
one recording the move into the terminal column — not from `created`. A task
that ran for three weeks and finished this morning must stay visible; keying
off `created` would archive it the moment it completed.

Step queues are never passed in. A task sitting in a step queue is backlog,
not garbage, however long it has sat there.

Knows only ports, models and `ids` — never a driver, like the reconcilers it
mirrors.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from harness.ids import new_lock_id
from harness.models import ARCHIVED, HistoryEntry, Task, append_history
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue

ACTOR = "retention"

DEFAULT_RETENTION_DAYS = 2
"""Days a settled task stays on the board. Two days holds a root producing a
handful of settled tasks a day at ~10 visible: yesterday's runs are still there
in the morning, and everything older is one `archived/` lookup away."""


def _moment(text: str) -> datetime | None:
    """Parse an ISO-8601 harness timestamp, or None if it will not parse.

    `.replace("Z", "+00:00")` is the codebase's existing idiom (see
    `ports/triggers.py`, `drivers/scheduled_trigger.py`). A naive result is
    pinned to UTC so it can never raise on comparison with an aware one —
    every harness timestamp comes from `Clock.now()` and carries the `Z`, but
    a hand-edited task file must not be able to crash the sweep.
    """
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def settled_at(task: Task) -> str:
    """When the task reached its terminal column: the last history entry's
    timestamp, falling back to `created` when the history is empty."""
    return task.history[-1].at if task.history else task.created


class RetentionReconciler:
    def __init__(
        self,
        *,
        queues: list[TaskQueue],
        archived: TaskQueue,
        days: int,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._queues = queues
        self._archived = archived
        self._days = days
        self._events = events
        self._clock = clock

    def tick(self) -> bool:
        """Sweep every terminal queue. True if anything was archived."""
        now = _moment(self._clock.now())
        if now is None:  # unreachable with a real Clock; not worth crashing over
            return False
        cutoff = now - timedelta(days=self._days)

        archived_any = False
        for queue in self._queues:
            for task in queue.list():
                moment = _moment(settled_at(task))
                if moment is None or moment > cutoff:
                    # Unparseable: leave it be, loudly visible on the board.
                    # Inside the window: not yet ours.
                    continue
                if self._archive(queue, task):
                    archived_any = True
        return archived_any

    def _archive(self, queue: TaskQueue, task: Task) -> bool:
        claimed = queue.claim(task, new_lock_id())
        if claimed is None:
            return False  # lost a race (a concurrent housekeeping loop)
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=claimed.status,
            to_step=None,
            reason=f"retention: settled more than {self._days}d ago",
        )
        resolved = append_history(replace(claimed, status=ARCHIVED, lock_id=None), entry)
        queue.transfer(resolved, self._archived)
        self._events.emit(
            "archived", task_id=task.id, queue="archived", task=resolved.to_dict()
        )
        return True
