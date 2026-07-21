"""In-memory read model of the board.

Built from two sources: a one-time hydration from the queues at startup, and
after that only from the stream of events. Touches ports exclusively — it knows
nothing about drivers.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from harness.models import END, Task, Workflow
from harness.ports.board import (
    DONE_COLUMN,
    FAILED_COLUMN,
    TODO_COLUMN,
    UNKNOWN_WORKFLOW,
    Board,
    BoardColumn,
    BoardTab,
    BoardView,
)
from harness.ports.queue import TaskQueue


def column_order(workflow: Workflow) -> tuple[str, ...]:
    """Steps in order of reachability from the start, then done and failed.

    Backward edges are ignored — a step already placed is never moved.
    Otherwise the column order would depend on which way the search went.
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

    return (TODO_COLUMN,) + tuple(order) + (DONE_COLUMN, FAILED_COLUMN)


class BoardProjection(BoardView):
    def __init__(self, workflows: Sequence[Workflow]) -> None:
        self._orders: dict[str, tuple[str, ...]] = {
            workflow.name: column_order(workflow) for workflow in workflows
        }
        # A task can only end up under an unrecognized template via a dispatch
        # failure or historical done/failed data from a removed definition —
        # never legitimately mid-flight — except for the narrow window before
        # the dispatcher's first tick, where hydrate() sees it still sitting
        # in `todo`. TODO_COLUMN must stay in this tuple or such a task is
        # silently dropped from the board until the dispatcher fails it.
        self._orders[UNKNOWN_WORKFLOW] = (TODO_COLUMN, DONE_COLUMN, FAILED_COLUMN)
        self._tasks: dict[str, Task] = {}
        self._locations: dict[str, tuple[str, str]] = {}
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
        """Build the initial state from the queues.

        Called only AFTER recovery — which returns tasks from .processing/ back
        into the queues, so list() sees them and nothing in flight is lost.
        """
        for step, queue in step_queues.items():
            for task in queue.list():
                self._store(step, task)
        for task in done.list():
            self._store(DONE_COLUMN, task)
        for task in failed.list():
            self._store(FAILED_COLUMN, task)
        for task in inbox.list():
            # A fresh task (never dispatched) shows in `todo`; one transiting the
            # inbox between steps keeps the column of the step it just left.
            self._store(task.status if task.status is not None else TODO_COLUMN, task)
        self._bump()

    def apply(self, column: str, task: Task) -> None:
        """Record that the task is now in the given column."""
        self._store(column, task)
        self._bump()

    def snapshot(self) -> Board:
        tabs = []
        for tab_name, order in self._orders.items():
            if tab_name == UNKNOWN_WORKFLOW and not any(
                location[0] == UNKNOWN_WORKFLOW for location in self._locations.values()
            ):
                continue  # FR-4: omit the unknown tab from the strip when empty
            columns = tuple(
                BoardColumn(
                    name=column_name,
                    tasks=tuple(
                        sorted(
                            (
                                task
                                for task_id, task in self._tasks.items()
                                if self._locations[task_id] == (tab_name, column_name)
                            ),
                            key=lambda task: (task.created, task.id),
                        )
                    ),
                )
                for column_name in order
            )
            tabs.append(BoardTab(name=tab_name, columns=columns))
        tabs.sort(key=lambda tab: tab.name)
        return Board(revision=self._revision, workflows=tuple(tabs))

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    async def subscribe(self) -> AsyncIterator[int]:
        """Stream of revisions. The queue is bounded — a slow client stalls no one."""
        inbox: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
        self._subscribers.add(inbox)
        try:
            yield self._revision
            while True:
                yield await inbox.get()
        finally:
            self._subscribers.discard(inbox)

    def _store(self, column: str, task: Task) -> None:
        tab = task.workflow_template if task.workflow_template in self._orders else UNKNOWN_WORKFLOW
        order = self._orders[tab]
        if column not in order:
            return
        self._tasks[task.id] = task
        self._locations[task.id] = (tab, column)

    def _bump(self) -> None:
        self._revision += 1
        for inbox in self._subscribers:
            try:
                inbox.put_nowait(self._revision)
            except asyncio.QueueFull:
                # The queue holds one (stale) revision. Replace it with a fresh
                # one so that after waking, a slow subscriber sees the latest
                # state, not a stale intermediate step.
                try:
                    inbox.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    inbox.put_nowait(self._revision)
                except asyncio.QueueFull:
                    pass
