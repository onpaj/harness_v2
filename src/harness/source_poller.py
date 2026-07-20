"""SourcePoller: the core that fills the inbox from the task source.

A second producer of the same queue alongside `harness submit`. The core stays
GitHub-blind — the poller knows only ports (`TaskSource`, `TaskQueue`,
`EventSink`), never a driver.
"""

from __future__ import annotations

from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue
from harness.ports.source import TaskSource


class SourcePoller:
    def __init__(
        self, *, source: TaskSource, inbox: TaskQueue, events: EventSink
    ) -> None:
        self._source = source
        self._inbox = inbox
        self._events = events

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

        for task in tasks:
            self._inbox.put(task)
            self._events.emit(
                "ingested", task_id=task.id, queue="tasks", task=task.to_dict()
            )
        return bool(tasks)
