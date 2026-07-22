"""Wiring. The one place where the ports meet concrete drivers."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from harness.behaviors.agent import ClaudeCliBehavior
from harness.behaviors.landing import LandingBehavior
from harness.behaviors.resolve_conflict import ResolveConflictBehavior
from harness.consumer import Consumer
from harness.dispatcher import Dispatcher
from harness.drivers.composite_events import CompositeEventSink
from harness.drivers.dummy_behavior import DummyBehavior
from harness.drivers.fifo_strategy import FifoStrategy
from harness.drivers.fs_queue import FilesystemTaskQueue
from harness.drivers.fs_workflows import (
    FilesystemWorkflowRepository,
    ServedWorkflowRepository,
)
from harness.drivers.memory import (
    MemoryArtifactStore,
    MemoryForge,
    MemoryIssueTracker,
    MemoryWorkspace,
)
from harness.drivers.projection_events import ProjectionSink
from harness.drivers.source_reflector import SourceReflectorSink
from harness.drivers.stage_output import StageOutputProjection
from harness.drivers.stdout_events import StdoutEventSink
from harness.drivers.system_clock import SystemClock
from harness.healer import Healer
from harness.models import Workflow
from harness.ports.agent import AgentCatalog, AgentRunner
from harness.ports.artifacts import ArtifactStore, ArtifactView
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.forge import Forge
from harness.ports.issues import IssueTracker
from harness.ports.logs import StageOutputView
from harness.ports.merge import MergeChecker
from harness.ports.queue import TaskQueue
from harness.ports.source import TaskSource
from harness.ports.workflows import WorkflowNotFound
from harness.ports.workspace import Workspace
from harness.projection import BoardProjection
from harness.ports.control import TaskControl
from harness.merge_reconciler import MergeReconciler
from harness.source_poller import SourcePoller
from harness.task_control import TaskControlService

LANDING_STEP = "land"
"""The step to which the wiring assigns LandingBehavior instead of DummyBehavior."""

RESOLVE_STEP = "resolve"
"""The step to which the wiring assigns ResolveConflictBehavior, when a catalog
is configured — the resolver workflow's first step."""


@dataclass(frozen=True)
class HealConfig:
    """Enables the healer: an agent assigned to the `failed/` queue.

    `repository` is the slug the issue is opened on (the harness's own repo);
    `spec_name` names the persona in the catalog; `labels` are added to the
    opened issue. Absent → no healer, `failed/` stays a dead-end terminal.
    """

    repository: str
    spec_name: str = "healer"
    labels: tuple[str, ...] = ("harness:self-heal",)
    timeout: float = 1800.0


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
    def archived(self) -> Path:
        return self.root / "archived"

    @property
    def healed(self) -> Path:
        return self.root / "healed"

    @property
    def heal_scratch(self) -> Path:
        return self.root / "heal"

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
        workflows: dict[str, Workflow],
        dispatcher: Dispatcher,
        consumers: list[Consumer],
        inbox: TaskQueue,
        step_queues: dict[str, TaskQueue],
        done: TaskQueue,
        failed: TaskQueue,
        projection: BoardProjection,
        artifacts: ArtifactView,
        stage_output: StageOutputView,
        control: TaskControl,
        events: EventSink,
        clock: Clock,
        pollers: list[SourcePoller] | None = None,
        archived: TaskQueue | None = None,
        reconciler: MergeReconciler | None = None,
        healer: Healer | None = None,
        healed: TaskQueue | None = None,
    ) -> None:
        self.layout = layout
        self.workflows = workflows
        self.dispatcher = dispatcher
        self.consumers = consumers
        self.pollers = pollers or []
        self.healer = healer
        self.projection = projection
        self.artifacts = artifacts
        self.stage_output = stage_output
        self.control = control
        self._inbox = inbox
        self._step_queues = step_queues
        self._done = done
        self._failed = failed
        self._healed = healed
        self._events = events
        self._clock = clock
        self.archived = archived
        self.reconciler = reconciler

    def recover(self) -> int:
        # `done` is always recovered, whether or not a reconciler is wired this
        # run: a `.processing/` file there can only have been left by a
        # MergeReconciler claim, but it may outlive the run that created it (the
        # operator could disable reconciliation between restarts). Recovering an
        # idle queue is a no-op, so this is free when reconciliation is off.
        queues = [self._inbox, *self._step_queues.values(), self._done]
        # With a healer wired, `failed/` is a consumed queue too — a crash mid-heal
        # leaves the task in `failed/.processing/`, so it must be recovered like any
        # other. Without a healer, `failed/` is never claimed and this is a no-op.
        if self.healer is not None:
            queues.append(self._failed)
        total = sum(queue.recover() for queue in queues)
        if total:
            self._events.emit("recovered", count=total)
        return total

    def _seed_pollers(self) -> None:
        """Teach the pollers which source identities are already ingested.

        Runs after `recover()` (so in-flight tasks are back in their queues and
        visible to `list()`), before the loops start. This is what makes source
        deduplication survive a restart: a GitHub issue whose task is already on
        disk — in any queue, done or failed — is never ingested a second time.
        """
        if not self.pollers:
            return
        existing = [
            *self._inbox.list(),
            *(task for queue in self._step_queues.values() for task in queue.list()),
            *self._done.list(),
            *self._failed.list(),
        ]
        for poller in self.pollers:
            poller.seed(existing)

    async def run(
        self,
        poll_interval: float = 0.2,
        source_interval: float = 30.0,
        reconcile_interval: float = 300.0,
        stop: asyncio.Event | None = None,
    ) -> None:
        # The internal loops (dispatcher, consumers) poll local queues off disk,
        # so a tight `poll_interval` keeps the board responsive. A `TaskSource`,
        # by contrast, is a remote API with rate limits (GitHub), so its loop
        # gets its own, much slower `source_interval`. The reconciler is also a
        # remote API call, but a housekeeping sweep rather than a latency-
        # sensitive "pick up new work" path, so it gets its own, longer
        # `reconcile_interval` still — distinct from `source_interval`.
        stop = stop or asyncio.Event()
        self.recover()
        self.projection.hydrate(
            inbox=self._inbox,
            step_queues=self._step_queues,
            done=self._done,
            failed=self._failed,
            archived=self.archived,
            healed=self._healed,
        )
        self._seed_pollers()
        self._events.emit("started", workflows=sorted(self.workflows))
        await asyncio.gather(
            self._dispatcher_loop(poll_interval, stop),
            *(
                self._consumer_loop(consumer, poll_interval, stop)
                for consumer in self.consumers
                for _ in range(self._max_parallel_for(consumer.step))
            ),
            *(self._source_loop(poller, source_interval, stop) for poller in self.pollers),
            *(
                [self._reconcile_loop(self.reconciler, reconcile_interval, stop)]
                if self.reconciler is not None
                else []
            ),
            *([self._heal_loop(poll_interval, stop)] if self.healer is not None else []),
        )
        self._events.emit("stopped")

    def _max_parallel_for(self, step: str) -> int:
        """The concurrency ceiling for a step's shared consumer.

        Step queues are unioned by name across served workflows, so a single
        consumer serves every workflow that has the step. Its ceiling is the
        largest limit any of those workflows assigns the step (default 1)."""
        return max(
            (
                workflow.max_parallel_for(step)
                for workflow in self.workflows.values()
                if step in workflow.steps()
            ),
            default=1,
        )

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

    async def _source_loop(
        self, poller: SourcePoller, source_interval: float, stop: asyncio.Event
    ) -> None:
        while not stop.is_set():
            if not poller.tick():
                await asyncio.sleep(source_interval)
            else:
                await asyncio.sleep(0)

    async def _reconcile_loop(
        self, reconciler: MergeReconciler, reconcile_interval: float, stop: asyncio.Event
    ) -> None:
        while not stop.is_set():
            if not reconciler.tick():
                await asyncio.sleep(reconcile_interval)

    async def _heal_loop(self, poll_interval: float, stop: asyncio.Event) -> None:
        assert self.healer is not None
        while not stop.is_set():
            if not await self.healer.tick():
                await asyncio.sleep(poll_interval)
            else:
                await asyncio.sleep(0)


def build(
    root: Path,
    workflows: str | Sequence[str] | None = None,
    *,
    events: EventSink | None = None,
    clock: Clock | None = None,
    behavior: ConsumerBehavior | None = None,
    workspace: Workspace | None = None,
    artifacts: ArtifactStore | None = None,
    forge: Forge | None = None,
    runner: AgentRunner | None = None,
    catalog: AgentCatalog | None = None,
    agent_timeout: float = 1800.0,
    artifact_view: ArtifactView | None = None,
    sources: list[TaskSource] | None = None,
    merge_checker: MergeChecker | None = None,
    landing_step: str = LANDING_STEP,
    delay: float = 5.0,
    request_changes_once_at: str | None = None,
    issue_tracker: IssueTracker | None = None,
    heal: HealConfig | None = None,
) -> Harness:
    layout = HarnessLayout(Path(root))
    events = events or StdoutEventSink()
    clock = clock or SystemClock()
    sources = sources or []
    strategy = FifoStrategy()

    # Working drivers: the default is in-memory (the substrate of the dummy
    # behavior, just as DummyBehavior itself is a fake). The real run (`harness
    # run`) and the git smoke inject git/fs/fake here — a swap of the driver,
    # not its surroundings.
    workspace = workspace or MemoryWorkspace()
    artifacts = artifacts or MemoryArtifactStore()
    forge = forge or MemoryForge()

    # The served set: the workflow(s) this harness actually routes. `None`
    # means workflow-less (FR-6) — the harness runs its catalog agents with no
    # workflow at all. A named-but-unserved workflow still fails fast at
    # dispatch via ServedWorkflowRepository, so no dispatcher change is needed.
    if workflows is None:
        names: tuple[str, ...] = ()
    elif isinstance(workflows, str):
        names = (workflows,)
    else:
        names = tuple(workflows)

    raw_workflows = FilesystemWorkflowRepository(layout.workflows)
    resolved = {name: raw_workflows.get(name) for name in names}  # WorkflowNotFound => fail fast
    served_workflows = ServedWorkflowRepository(raw_workflows, tuple(resolved))

    # One tab per discovered workflow definition (read side only — dispatch below
    # stays keyed to the served `resolved` set). names() may include a broken
    # definition (it fails loud only from get()), so skip those defensively; the
    # served workflows always get a tab even if a rename made them undiscoverable.
    discovered: dict[str, Workflow] = dict(resolved)
    for name in raw_workflows.names():
        if name in discovered:
            continue
        try:
            discovered[name] = raw_workflows.get(name)
        except WorkflowNotFound:
            continue  # unreadable file during discovery: skipped, not fatal

    # FR-6/FR-7: the live queue set is the union of every step reachable in the
    # served workflow(s) plus every agent declared under agents/ — so a
    # workflow-less catalog agent still gets a queue, a consumer and a column.
    # Unserved (but on-disk) workflows contribute a read-only board tab (above)
    # but no live queue: dispatch stays keyed to the served `resolved` set.
    known_steps: set[str] = set()
    for workflow in resolved.values():
        known_steps |= set(workflow.steps())
    if catalog is not None:
        known_steps |= set(catalog.names())

    steps = tuple(sorted(known_steps))

    projection = BoardProjection(
        steps=steps,
        workflows=list(discovered.values()),
        include_healed=heal is not None,
    )
    stage_output = StageOutputProjection()
    # The reflector comes after ProjectionSink: the outward projection must not
    # get ahead of the board. The stage-output projection sits alongside the
    # board sink — it feeds a separate read model (live agent output) and never
    # touches the board.
    events = CompositeEventSink(
        events,
        ProjectionSink(projection),
        stage_output,
        SourceReflectorSink(sources),
    )

    failed = FilesystemTaskQueue(name="failed", root=layout.failed, events=events)
    done = FilesystemTaskQueue(name="done", root=layout.done, events=events)
    inbox = FilesystemTaskQueue(
        name="tasks", root=layout.tasks, events=events, quarantine=failed
    )
    # Step queues, consumers and board columns are unioned across every served
    # workflow (e.g. the primary and the resolver workflow) so a task carrying a
    # different `workflow_template` still gets a working queue — the dispatcher
    # already resolves the workflow per task, this is the only piece that
    # otherwise assumed a single workflow.
    step_queues = {
        step: FilesystemTaskQueue(
            name=step, root=layout.queues / step, events=events, quarantine=failed
        )
        for step in steps
    }

    # The archived queue and reconciler only exist when a merge_checker is
    # supplied — a run without GitHub wiring pays nothing for this feature.
    archived: TaskQueue | None = None
    reconciler: MergeReconciler | None = None
    if merge_checker is not None:
        archived = FilesystemTaskQueue(
            name="archived", root=layout.archived, events=events
        )
        reconciler = MergeReconciler(
            done=done,
            archived=archived,
            checker=merge_checker,
            events=events,
            clock=clock,
        )

    # Read side of artifacts: when passed in (phase 3: `WorktreeArtifactView`),
    # both landing and `api/` get it; otherwise the write store (also an
    # `ArtifactView`) stays, as in phase 2.
    view: ArtifactView = artifact_view or artifacts

    work = behavior or DummyBehavior(
        clock=clock,
        workspace=workspace,
        artifacts=artifacts,
        delay=delay,
        request_changes_once_at=request_changes_once_at,
    )
    # When landing is handed a read-side view of the worktree, the artifacts are
    # already versioned there — landing doesn't copy them, it just opens the PR.
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
        if step == RESOLVE_STEP and catalog is not None:
            return ResolveConflictBehavior(
                clock=clock,
                workspace=workspace,
                runner=runner,
                spec=catalog.get(step),
                events=events,
                timeout=agent_timeout,
            )
        if catalog is not None:
            # Missing spec → AgentNotFound surfaces already at build time (fail fast).
            spec = catalog.get(step)
            effective_timeout = (
                spec.timeout if spec.timeout is not None else agent_timeout
            )
            return ClaudeCliBehavior(
                clock=clock,
                workspace=workspace,
                runner=runner,
                spec=spec,
                events=events,
                timeout=effective_timeout,
            )
        return work

    dispatcher = Dispatcher(
        inbox=inbox,
        step_queues=step_queues,
        done=done,
        failed=failed,
        workflows=served_workflows,
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

    pollers = [
        SourcePoller(source=source, inbox=inbox, events=events) for source in sources
    ]

    control = TaskControlService(
        inbox=inbox, failed=failed, events=events, clock=clock
    )

    # The healer: an agent assigned to the `failed/` queue. It reads a failed
    # task, drafts + files an issue on the harness repo, and settles the task
    # onto the terminal `healed/`. It reuses the agent runner/catalog (persona as
    # data) — so it needs both; the issue tracker defaults to the in-memory fake
    # (offline / tests), swapped for GitHub by `cli._run`.
    healed_queue: TaskQueue | None = None
    healer: Healer | None = None
    if heal is not None:
        if runner is None or catalog is None:
            raise ValueError(
                "self-healing needs an agent runner and a catalog "
                "(pass runner= and catalog=, or leave heal=None)"
            )
        healed_queue = FilesystemTaskQueue(
            name="healed", root=layout.healed, events=events
        )
        healer = Healer(
            failed=failed,
            healed=healed_queue,
            runner=runner,
            spec=catalog.get(heal.spec_name),
            tracker=issue_tracker or MemoryIssueTracker(),
            repo=heal.repository,
            scratch_root=layout.heal_scratch,
            strategy=strategy,
            events=events,
            clock=clock,
            labels=heal.labels,
            timeout=heal.timeout,
        )

    return Harness(
        layout=layout,
        workflows=resolved,
        dispatcher=dispatcher,
        consumers=consumers,
        inbox=inbox,
        step_queues=step_queues,
        done=done,
        failed=failed,
        projection=projection,
        artifacts=view,
        stage_output=stage_output,
        control=control,
        events=events,
        clock=clock,
        pollers=pollers,
        archived=archived,
        reconciler=reconciler,
        healer=healer,
        healed=healed_queue,
    )
