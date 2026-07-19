"""Wiring. Jediné místo, kde se porty potkají s konkrétními drivery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from harness.consumer import Consumer
from harness.dispatcher import Dispatcher
from harness.drivers.composite_events import CompositeEventSink
from harness.drivers.dummy_behavior import DummyBehavior
from harness.drivers.fifo_strategy import FifoStrategy
from harness.drivers.fs_queue import FilesystemTaskQueue
from harness.drivers.fs_workflows import FilesystemWorkflowRepository
from harness.drivers.projection_events import ProjectionSink
from harness.drivers.stdout_events import StdoutEventSink
from harness.drivers.system_clock import SystemClock
from harness.models import Workflow
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue
from harness.projection import BoardProjection


@dataclass(frozen=True)
class HarnessLayout:
    root: Path

    @property
    def workflows(self) -> Path:
        return self.root / "workflows"

    @property
    def tasks(self) -> Path:
        return self.root / "tasks"

    @property
    def queues(self) -> Path:
        return self.root / "queues"

    @property
    def done(self) -> Path:
        return self.root / "done"

    @property
    def failed(self) -> Path:
        return self.root / "failed"


class Harness:
    def __init__(
        self,
        *,
        layout: HarnessLayout,
        workflow: Workflow,
        dispatcher: Dispatcher,
        consumers: list[Consumer],
        inbox: TaskQueue,
        step_queues: dict[str, TaskQueue],
        done: TaskQueue,
        failed: TaskQueue,
        projection: BoardProjection,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self.layout = layout
        self.workflow = workflow
        self.dispatcher = dispatcher
        self.consumers = consumers
        self.projection = projection
        self._inbox = inbox
        self._step_queues = step_queues
        self._done = done
        self._failed = failed
        self._events = events
        self._clock = clock

    def recover(self) -> int:
        queues = [self._inbox, *self._step_queues.values()]
        total = sum(queue.recover() for queue in queues)
        if total:
            self._events.emit("recovered", count=total)
        return total

    async def run(
        self, poll_interval: float = 0.2, stop: asyncio.Event | None = None
    ) -> None:
        stop = stop or asyncio.Event()
        self.recover()
        self.projection.hydrate(
            inbox=self._inbox,
            step_queues=self._step_queues,
            done=self._done,
            failed=self._failed,
        )
        self._events.emit("started", workflow=self.workflow.name)
        await asyncio.gather(
            self._dispatcher_loop(poll_interval, stop),
            *(self._consumer_loop(consumer, poll_interval, stop) for consumer in self.consumers),
        )
        self._events.emit("stopped")

    async def _dispatcher_loop(self, poll_interval: float, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if not self.dispatcher.tick():
                await asyncio.sleep(poll_interval)
            else:
                await asyncio.sleep(0)

    async def _consumer_loop(
        self, consumer: Consumer, poll_interval: float, stop: asyncio.Event
    ) -> None:
        while not stop.is_set():
            if not await consumer.tick():
                await asyncio.sleep(poll_interval)
            else:
                await asyncio.sleep(0)


def build(
    root: Path,
    workflow_name: str,
    *,
    events: EventSink | None = None,
    clock: Clock | None = None,
    behavior: ConsumerBehavior | None = None,
    delay: float = 5.0,
    request_changes_once_at: str | None = None,
) -> Harness:
    layout = HarnessLayout(Path(root))
    events = events or StdoutEventSink()
    clock = clock or SystemClock()
    strategy = FifoStrategy()

    workflows = FilesystemWorkflowRepository(layout.workflows)
    workflow = workflows.get(workflow_name)

    projection = BoardProjection(workflow)
    events = CompositeEventSink(events, ProjectionSink(projection))

    failed = FilesystemTaskQueue(name="failed", root=layout.failed, events=events)
    done = FilesystemTaskQueue(name="done", root=layout.done, events=events)
    inbox = FilesystemTaskQueue(
        name="tasks", root=layout.tasks, events=events, quarantine=failed
    )
    step_queues = {
        step: FilesystemTaskQueue(
            name=step, root=layout.queues / step, events=events, quarantine=failed
        )
        for step in workflow.steps()
    }

    behavior = behavior or DummyBehavior(
        clock=clock, delay=delay, request_changes_once_at=request_changes_once_at
    )

    dispatcher = Dispatcher(
        inbox=inbox,
        step_queues=step_queues,
        done=done,
        failed=failed,
        workflows=workflows,
        strategy=strategy,
        events=events,
        clock=clock,
    )

    consumers = [
        Consumer(
            step=step,
            queue=queue,
            inbox=inbox,
            failed=failed,
            behavior=behavior,
            strategy=strategy,
            events=events,
            clock=clock,
        )
        for step, queue in step_queues.items()
    ]

    return Harness(
        layout=layout,
        workflow=workflow,
        dispatcher=dispatcher,
        consumers=consumers,
        inbox=inbox,
        step_queues=step_queues,
        done=done,
        failed=failed,
        projection=projection,
        events=events,
        clock=clock,
    )
