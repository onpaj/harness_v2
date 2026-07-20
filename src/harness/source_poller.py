"""SourcePoller: the core that fills the inbox from the task source.

A second producer of the same queue alongside `harness submit`. The core stays
GitHub-blind — the poller knows only ports (`TaskSource`, `TaskQueue`,
`EventSink`), never a driver.

Deduplication is by the task's unique identity in its source (`source_key`,
read from `task.data.source`), not by anything the harness assigns. The label
swap in `GithubTaskSource.poll()` gives at-most-once *within* a run, but the
in-process ledger is lost on restart and the GitHub label state can drift (a
failed issue re-labelled `harness:todo`, read-after-write lag). So the poller
keeps a persistent `_seen` set, seeded at startup from the tasks already on
disk (`seed`), and never ingests a source identity it has already ingested —
one GitHub issue yields exactly one task across the lifetime of the harness.
"""

from __future__ import annotations

from collections.abc import Iterable

from harness.models import Task
from harness.ports.board import TODO_COLUMN
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue
from harness.ports.source import TaskSource, source_key


class SourcePoller:
    def __init__(
        self, *, source: TaskSource, inbox: TaskQueue, events: EventSink
    ) -> None:
        self._source = source
        self._inbox = inbox
        self._events = events
        # Source identities already ingested. Seeded from disk at startup so it
        # survives restarts; a task without a source (`harness submit`) has no
        # key and is never deduplicated.
        self._seen: set[tuple] = set()

    def seed(self, tasks: Iterable[Task]) -> None:
        """Register already-ingested tasks so their sources aren't re-ingested.

        Called once at startup with every task on disk (across all queues, done
        and failed). This is what makes deduplication survive a restart.
        """
        for task in tasks:
            key = source_key(task)
            if key is not None:
                self._seen.add(key)

    def tick(self) -> bool:
        """Fetch tasks from the source and put them into the inbox. True if something arrived.

        We catch and log an exception from `poll()` (GitHub down); the tick then
        returns False, the loop sleeps and tries again — a source failure must
        not stop the orchestration.
        """
        try:
            tasks = self._source.poll()
        except Exception as error:  # noqa: BLE001 - a source failure won't stop the loop
            self._events.emit(
                "source_error", source=self._source.kind, error=str(error)
            )
            return False

        ingested = False
        for task in tasks:
            key = source_key(task)
            if key is not None and key in self._seen:
                # Already ingested this source identity (restart, label drift or
                # read-after-write lag). Drop the duplicate — one source item is
                # one task.
                self._events.emit(
                    "duplicate_ignored",
                    task_id=task.id,
                    source=self._source.kind,
                )
                continue
            if key is not None:
                self._seen.add(key)
            self._inbox.put(task)
            # A freshly ingested task is unstarted (status=None): on the board it
            # belongs in `todo`, not the physical inbox queue name (`tasks`),
            # which is not a column.
            self._events.emit(
                "ingested", task_id=task.id, queue=TODO_COLUMN, task=task.to_dict()
            )
            ingested = True
        return ingested
