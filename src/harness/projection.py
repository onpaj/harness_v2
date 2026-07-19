"""In-memory read model boardu.

Staví se ze dvou zdrojů: jednorázové hydratace z front při startu a poté
už jen z proudu eventů. Sahá výhradně na porty — o driverech neví.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from harness.models import END, Task, Workflow
from harness.ports.board import (
    DONE_COLUMN,
    FAILED_COLUMN,
    Board,
    BoardColumn,
    BoardView,
)
from harness.ports.queue import TaskQueue


def column_order(workflow: Workflow) -> tuple[str, ...]:
    """Kroky v pořadí dosažitelnosti ze startu, pak done a failed.

    Zpětné hrany se ignorují — krok už jednou zařazený se nepřesouvá.
    Jinak by pořadí sloupců záviselo na tom, kudy se prohledávání vydalo.
    """
    order: list[str] = []
    pending: list[str] = [workflow.start]

    while pending:
        step = pending.pop(0)
        if step == END or step in order:
            continue
        order.append(step)
        for transition in workflow.transitions:
            if transition.from_step == step and transition.to_step not in order:
                pending.append(transition.to_step)

    for step in workflow.steps():
        if step not in order:
            order.append(step)

    return tuple(order) + (DONE_COLUMN, FAILED_COLUMN)


class BoardProjection(BoardView):
    def __init__(self, workflow: Workflow) -> None:
        self._order = column_order(workflow)
        self._tasks: dict[str, Task] = {}
        self._columns: dict[str, str] = {}
        self._revision = 0
        self._subscribers: set[asyncio.Queue[int]] = set()

    def hydrate(
        self,
        *,
        inbox: TaskQueue,
        step_queues: dict[str, TaskQueue],
        done: TaskQueue,
        failed: TaskQueue,
    ) -> None:
        """Postav výchozí stav z front.

        Volá se až PO recovery — ta vrátí tasky z .processing/ zpátky do
        front, takže je list() uvidí a nic v letu se neztratí.
        """
        for step, queue in step_queues.items():
            for task in queue.list():
                self._store(step, task)
        for task in done.list():
            self._store(DONE_COLUMN, task)
        for task in failed.list():
            self._store(FAILED_COLUMN, task)
        for task in inbox.list():
            if task.status is not None:
                self._store(task.status, task)
        self._bump()

    def apply(self, column: str, task: Task) -> None:
        """Zaznamenej, že task je nově v daném sloupci."""
        self._store(column, task)
        self._bump()

    def snapshot(self) -> Board:
        columns = []
        for name in self._order:
            tasks = tuple(
                sorted(
                    (
                        task
                        for task_id, task in self._tasks.items()
                        if self._columns[task_id] == name
                    ),
                    key=lambda task: (task.created, task.id),
                )
            )
            columns.append(BoardColumn(name=name, tasks=tasks))
        return Board(revision=self._revision, columns=tuple(columns))

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    async def subscribe(self) -> AsyncIterator[int]:
        """Proud revizí. Fronta je bounded — pomalý klient nikoho nebrzdí."""
        inbox: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
        self._subscribers.add(inbox)
        try:
            yield self._revision
            while True:
                yield await inbox.get()
        finally:
            self._subscribers.discard(inbox)

    def _store(self, column: str, task: Task) -> None:
        if column not in self._order:
            return
        self._tasks[task.id] = task
        self._columns[task.id] = column

    def _bump(self) -> None:
        self._revision += 1
        for inbox in self._subscribers:
            try:
                inbox.put_nowait(self._revision)
            except asyncio.QueueFull:
                # Fronta drží jednu (stale) revizi. Nahraď ji čerstvou, ať
                # pomalý odběratel po probuzení uvidí nejnovější stav, ne
                # zastaralý mezikrok.
                try:
                    inbox.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    inbox.put_nowait(self._revision)
                except asyncio.QueueFull:
                    pass
