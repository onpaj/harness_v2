"""Wiring. Jediné místo, kde se porty potkají s konkrétními drivery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from harness.behaviors.agent import ClaudeCliBehavior
from harness.behaviors.landing import LandingBehavior
from harness.consumer import Consumer
from harness.dispatcher import Dispatcher
from harness.drivers.composite_events import CompositeEventSink
from harness.drivers.dummy_behavior import DummyBehavior
from harness.drivers.fifo_strategy import FifoStrategy
from harness.drivers.fs_queue import FilesystemTaskQueue
from harness.drivers.fs_workflows import FilesystemWorkflowRepository
from harness.drivers.memory import (
    MemoryArtifactStore,
    MemoryForge,
    MemoryWorkspace,
)
from harness.drivers.projection_events import ProjectionSink
from harness.drivers.stdout_events import StdoutEventSink
from harness.drivers.system_clock import SystemClock
from harness.models import Workflow
from harness.ports.agent import AgentCatalog, AgentRunner
from harness.ports.artifacts import ArtifactStore, ArtifactView
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.forge import Forge
from harness.ports.queue import TaskQueue
from harness.ports.workspace import Workspace
from harness.projection import BoardProjection

LANDING_STEP = "land"
"""Krok, kterému wiring přiřadí LandingBehavior místo DummyBehavior."""


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

    @property
    def worktrees(self) -> Path:
        return self.root / "worktrees"

    @property
    def agents(self) -> Path:
        return self.root / "agents"

    @property
    def repos(self) -> Path:
        return self.root / "repos.json"


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
        artifacts: ArtifactView,
        events: EventSink,
        clock: Clock,
    ) -> None:
        self.layout = layout
        self.workflow = workflow
        self.dispatcher = dispatcher
        self.consumers = consumers
        self.projection = projection
        self.artifacts = artifacts
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
    workspace: Workspace | None = None,
    artifacts: ArtifactStore | None = None,
    forge: Forge | None = None,
    runner: AgentRunner | None = None,
    catalog: AgentCatalog | None = None,
    agent_timeout: float = 600.0,
    artifact_view: ArtifactView | None = None,
    landing_step: str = LANDING_STEP,
    delay: float = 5.0,
    request_changes_once_at: str | None = None,
) -> Harness:
    layout = HarnessLayout(Path(root))
    events = events or StdoutEventSink()
    clock = clock or SystemClock()
    strategy = FifoStrategy()

    # Pracovní drivery: default je in-memory (substrát dummy behavioru, stejně
    # jako DummyBehavior sám je fake). Skutečný běh (`harness run`) i git smoke
    # si sem vstříknou git/fs/fake — záměna driveru, ne okolí.
    workspace = workspace or MemoryWorkspace()
    artifacts = artifacts or MemoryArtifactStore()
    forge = forge or MemoryForge()

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

    # Read-side artefaktů: když je předaný (fáze 3: `WorktreeArtifactView`),
    # dostane ho i landing i `api/`; jinak zůstává zápisový store (taky
    # `ArtifactView`), jako ve fázi 2.
    view: ArtifactView = artifact_view or artifacts

    work = behavior or DummyBehavior(
        clock=clock,
        workspace=workspace,
        artifacts=artifacts,
        delay=delay,
        request_changes_once_at=request_changes_once_at,
    )
    # Když je landingu předán read-side pohled na worktree, artefakty už tam
    # jsou versované — landing je nekopíruje, jen otevře PR.
    landing = LandingBehavior(
        clock=clock,
        workspace=workspace,
        artifacts=view,
        forge=forge,
        copy_artifacts=artifact_view is None,
    )

    def behavior_for(step: str) -> ConsumerBehavior:
        if step == landing_step:
            return landing
        if catalog is not None:
            # Chybějící spec → AgentNotFound probublá už při buildu (fail fast).
            return ClaudeCliBehavior(
                clock=clock,
                workspace=workspace,
                runner=runner,
                spec=catalog.get(step),
                timeout=agent_timeout,
            )
        return work

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
            behavior=behavior_for(step),
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
        artifacts=view,
        events=events,
        clock=clock,
    )
