"""IssueReconciler: retires tasks whose source issue was closed or deleted.

A periodic housekeeping sweep, structurally a sibling of `PrWatcher` — but where
`PrWatcher` scans only `done/` for a resolved *PR*, this scans every live queue
for a resolved *source issue*. The motivation: an issue can be closed (or the PR
merged, which auto-closes it) out from under a task that is still sitting on the
board — in `todo`, mid-workflow, in `done` or in `failed`. Such a task is stale:
the reason it existed is gone, so it should drop off the board.

Like every core loop it knows only ports, models and `ids` — never a driver.
"Removed from the dashboard" is the exact `archived/` semantics `PrWatcher` and
`MergeReconciler` already use: the task moves to `archived/` (crash-safe, off
every column, still gettable by id), it is never destroyed.

All-per-tick (like `PrWatcher`, not `MergeReconciler`'s one-per-tick): a closed
issue should clear promptly, and the loop runs on a slow housekeeping interval
so the extra reads are cheap. A per-task check failure is isolated — one
unreachable issue must not stop the sweep.
"""

from __future__ import annotations

from dataclasses import replace

from harness.ids import new_lock_id
from harness.models import ARCHIVED, HistoryEntry, Task, append_history
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.issue_state import IssueChecker
from harness.ports.queue import TaskQueue

ACTOR = "issue_reconciler"


class IssueReconciler:
    def __init__(
        self,
        *,
        queues: list[TaskQueue],
        archived: TaskQueue,
        checker: IssueChecker,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._queues = queues
        self._archived = archived
        self._checker = checker
        self._events = events
        self._clock = clock

    def tick(self) -> bool:
        """Check every task with a resolvable source issue. True if anything archived."""
        archived_any = False
        for queue in self._queues:
            for task in queue.list():
                try:
                    open_ = self._checker.is_open(task)
                except Exception as error:  # noqa: BLE001 - one bad check must not stop the tick
                    self._events.emit(
                        "issue_check_error", task_id=task.id, error=str(error)
                    )
                    continue
                if open_ is None or open_:
                    # None: no issue this checker can resolve (a submitted task, a
                    # foreign source). True: still open. Either way, leave it be.
                    continue
                if self._archive(queue, task):
                    archived_any = True
        return archived_any

    def _archive(self, queue: TaskQueue, task: Task) -> bool:
        claimed = queue.claim(task, new_lock_id())
        if claimed is None:
            return False  # lost a race (a consumer, or a concurrent housekeeping loop)
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=claimed.status,
            to_step=None,
            reason="source issue closed",
        )
        resolved = append_history(replace(claimed, status=ARCHIVED, lock_id=None), entry)
        queue.transfer(resolved, self._archived)
        self._events.emit(
            "archived", task_id=task.id, queue="archived", task=resolved.to_dict()
        )
        return True
