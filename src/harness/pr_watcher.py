"""PrWatcher: the core that archives a landed task once its PR resolves.

A periodic scan over already-terminal tasks (`done/`), never a claim/consume
cycle on a normal step queue — a PR can take days to close, and a workflow step
would block everything behind it in that queue for that long. Modeled directly
on `SourcePoller`: it knows only ports (`TaskQueue`, `Forge`, `EventSink`,
`Clock`), never a driver.
"""

from __future__ import annotations

from dataclasses import replace

from harness.ids import new_lock_id
from harness.models import ARCHIVED, HistoryEntry, Task, append_history
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.forge import Forge, PullRequestState
from harness.ports.queue import TaskQueue

ACTOR = "pr_watcher"


class PrWatcher:
    def __init__(
        self,
        *,
        done: TaskQueue,
        archived: TaskQueue,
        forge: Forge,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self._done = done
        self._archived = archived
        self._forge = forge
        self._events = events
        self._clock = clock

    def tick(self) -> bool:
        """Check every done task with a stored PR reference. True if anything archived."""
        archived_any = False
        for task in self._done.list():
            pr = task.data.get("pr")
            if not isinstance(pr, dict):
                continue  # never landed, or a task landed before this feature shipped
            try:
                state = self._forge.pull_request_state(task)
            except Exception as error:  # noqa: BLE001 - one bad check must not stop the tick
                self._events.emit("pr_watch_error", task_id=task.id, error=str(error))
                continue
            if state is PullRequestState.OPEN:
                continue  # still open, check again next tick
            if self._archive(task, state):
                archived_any = True
        return archived_any

    def _archive(self, task: Task, state: PullRequestState) -> bool:
        claimed = self._done.claim(task, new_lock_id())
        if claimed is None:
            return False  # lost a race (e.g. concurrent tick)
        resolution = "merged" if state is PullRequestState.MERGED else "closed"
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=claimed.status,
            to_step=None,
            reason=f"pr {resolution}",
        )
        resolved = append_history(replace(claimed, status=ARCHIVED, lock_id=None), entry)
        self._done.transfer(resolved, self._archived)
        self._events.emit(
            "archived",
            task_id=task.id,
            resolution=resolution,
            queue="archived",
            task=resolved.to_dict(),
        )
        return True
