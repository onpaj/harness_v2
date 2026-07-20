"""SourcePoller: jádro plnící inbox ze zdroje tasků.

Druhý producent téže fronty vedle `harness submit`. Jádro zůstává
GitHub-slepé — poller zná jen porty (`TaskSource`, `TaskQueue`, `EventSink`),
nikdy driver.
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
        """Přines tasky ze zdroje a vlož je do inboxu. True, když něco přišlo.

        Výjimku z `poll()` (GitHub dole) chytneme a zalogujeme; tik vrátí False,
        smyčka pak spí a zkusí to zas — pád zdroje nesmí zastavit orchestraci.
        """
        try:
            tasks = self._source.poll()
        except Exception as error:  # noqa: BLE001 - pád zdroje nezastaví smyčku
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
