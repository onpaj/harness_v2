import asyncio
import json

import pytest

from harness.app import (
    HarnessLayout,
    UnknownFinisherKind,
    build,
    validate_workflow_finishers,
)
from harness.behaviors.landing import LandingBehavior
from harness.drivers.fake_forge import FakeForge
from harness.drivers.memory import (
    FakeClock,
    FakeIssueChecker,
    FakeMergeChecker,
    MemoryAgentCatalog,
    MemoryEventSink,
    MemoryForge,
    MemoryRepositoryRegistry,
)
from harness.models import ARCHIVED, DONE, END, BehaviorResult, FinisherBinding, Task, Transition, Workflow
from harness.ports.agent import AgentRun, AgentSpec
from harness.ports.behavior import ConsumerBehavior
from harness.ports.board import UNKNOWN_WORKFLOW
from harness.ports.source import TaskSource


class ReplaySource(TaskSource):
    """Returns a task for the same source identity on every poll — the shape of
    an issue that keeps reappearing under the select label after a restart."""

    kind = "github"

    def __init__(self, issue):
        self._issue = issue
        self._n = 0

    def poll(self):
        self._n += 1
        return [
            Task(
                id=f"tsk_replay_{self._n}",
                workflow_template="default",
                created="2026-07-20T10:00:00Z",
                dedup_key=f"github:o/r:{self._issue}",
                data={"source": {"kind": "github", "repo": "o/r", "issue": self._issue}},
            )
        ]

    def report_progress(self, task, progress):  # pragma: no cover - not called
        pass

    def finish(self, task, result):  # pragma: no cover - not called
        pass

# `await runner` waits on the `asyncio.gather` inside Harness.run, which finishes
# only when both loops see `stop.is_set()`. If a regression broke that, `await
# runner` would hang forever and the test would freeze instead of failing.
# `asyncio.wait_for` turns that into a fast, clear failure.
RUNNER_TIMEOUT = 5.0

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "plan"},
    ],
}


RESOLVER_DEFINITION = {
    "name": "resolver",
    "start": "unblock",
    "transitions": [
        {"from": "unblock", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
    ],
}


def seed(tmp_path):
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))
    return layout


def test_build_creates_one_queue_per_step(tmp_path):
    seed(tmp_path)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert sorted(step for step in harness.workflows["default"].steps()) == ["plan", "review"]
    assert (tmp_path / "queues" / "plan").is_dir()
    assert (tmp_path / "queues" / "review").is_dir()
    assert not (tmp_path / "queues" / "end").exists()


def test_harness_known_steps_matches_the_live_step_queues(tmp_path):
    """`Harness.known_steps` is derived from `_step_queues`, not a second
    stored copy — it must always equal exactly the steps that have a live
    dispatch queue, the same set `Dispatcher.tick` routes into."""
    seed(tmp_path)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert harness.known_steps == {"plan", "review"}


def test_build_gives_every_discovered_workflow_a_board_tab(tmp_path):
    layout = seed(tmp_path)
    (layout.workflows / "hotfix.json").write_text(
        json.dumps({"name": "hotfix", "start": "patch", "transitions": []})
    )

    harness = build(tmp_path, "default", events=MemoryEventSink())

    board = harness.projection.snapshot()
    assert {tab.name for tab in board.workflows} == {"default", "hotfix"}
    # Only the active `--workflow` gets live step queues — dispatch is unchanged.
    assert set(harness._step_queues) == {"plan", "review"}
    assert len(harness.consumers) == 2


def test_build_creates_inbox_done_and_failed(tmp_path):
    seed(tmp_path)

    build(tmp_path, "default", events=MemoryEventSink())

    assert (tmp_path / "tasks").is_dir()
    assert (tmp_path / "done").is_dir()
    assert (tmp_path / "failed").is_dir()
    assert (tmp_path / "archived").is_dir()


def test_build_always_constructs_a_pr_watcher(tmp_path):
    seed(tmp_path)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert harness.pr_watcher is not None


def test_build_with_extra_workflow_unions_step_queues(tmp_path):
    layout = seed(tmp_path)
    (layout.workflows / "resolver.json").write_text(json.dumps(RESOLVER_DEFINITION))

    harness = build(
        tmp_path, ["default", "resolver"], events=MemoryEventSink()
    )

    assert (tmp_path / "queues" / "unblock").is_dir()
    assert (tmp_path / "queues" / "land").is_dir()
    # default: plan, review, land (3); resolver adds only unblock (land is shared).
    assert len(harness.consumers) == 4


async def test_resolver_task_flows_through_unblock_and_land_to_done(tmp_path):
    from harness.drivers.memory import (
        FakeAgentRunner,
        FakeClock,
        MemoryAgentCatalog,
        MemoryArtifactStore,
        MemoryForge,
        MemoryWorkspace,
    )

    layout = seed(tmp_path)
    (layout.workflows / "resolver.json").write_text(json.dumps(RESOLVER_DEFINITION))

    catalog = MemoryAgentCatalog(
        {
            "plan": AgentSpec(name="plan", prompt="p"),
            "review": AgentSpec(name="review", prompt="p"),
            "unblock": AgentSpec(name="unblock", prompt="p"),
        }
    )
    runner = FakeAgentRunner(default=AgentRun(DONE, "done"))
    workspace = MemoryWorkspace()
    events = MemoryEventSink()

    harness = build(
        tmp_path,
        ["default", "resolver"],
        events=events,
        clock=FakeClock(),
        workspace=workspace,
        artifacts=MemoryArtifactStore(),
        forge=MemoryForge(),
        catalog=catalog,
        runner=runner,
        delay=0.0,
    )

    task = Task(
        id="tsk_resolver_1",
        workflow_template="resolver",
        created="2026-07-21T10:00:00Z",
        repository="app",
        data={
            "branch": "harness/tsk_original",
            "source": {"kind": "pull-request-health", "repo": "o/r", "pr": 1, "url": "u", "base": "main"},
        },
    )
    (tmp_path / "tasks" / "tsk_resolver_1.json").write_text(json.dumps(task.to_dict()))

    stop = asyncio.Event()
    runner_task = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(400):
        await asyncio.sleep(0.01)
        if (tmp_path / "done" / "tsk_resolver_1.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner_task, timeout=RUNNER_TIMEOUT)

    assert (tmp_path / "done" / "tsk_resolver_1.json").exists()
    finished = Task.from_dict(
        json.loads((tmp_path / "done" / "tsk_resolver_1.json").read_text())
    )
    assert finished.status == "end"
    # Proves the `unblock` step actually ran through the dedicated
    # `UnblockPrBehavior` branch of `_inner_behavior_for`, not the generic
    # `ClaudeCliBehavior` fallback (which never calls `merge` at all — grep
    # confirms `behaviors/agent.py` has no `handle.merge` call). Two
    # independent fingerprints only `UnblockPrBehavior.run` could leave:
    #
    # 1. `WorkspaceHandle.merge` is called twice — once by `unblock` itself,
    #    once more by `land`'s base-branch sync (invariant #12). A generic
    #    `ClaudeCliBehavior` on `unblock` would leave exactly one, since it
    #    never merges at all.
    handle = workspace.handles["tsk_resolver_1"]
    assert handle.merges == ["main", "main"]
    # 2. The `unblock` step's own history entry carries the exact summary
    #    string `UnblockPrBehavior` writes on its clean-merge path
    #    (`unblock_pr.py`: `f"merged {base} cleanly, nothing to fix"`) —
    #    `ClaudeCliBehavior` has no code path that could ever produce it.
    unblock_entries = [entry for entry in finished.history if entry.actor == "consumer:unblock"]
    assert len(unblock_entries) == 1
    assert unblock_entries[0].summary == "merged main cleanly, nothing to fix"


def _unblock_behavior(tmp_path, *, spec):
    """The `UnblockPrBehavior` `build()` wires onto the `unblock` step."""
    from harness.drivers.memory import MemoryWorkspace

    layout = seed(tmp_path)
    (layout.workflows / "resolver.json").write_text(json.dumps(RESOLVER_DEFINITION))
    catalog = MemoryAgentCatalog(
        {
            "plan": AgentSpec(name="plan", prompt="p"),
            "review": AgentSpec(name="review", prompt="p"),
            "unblock": spec,
        }
    )
    harness = build(
        tmp_path,
        ["default", "resolver"],
        events=MemoryEventSink(),
        clock=FakeClock(),
        workspace=MemoryWorkspace(),
        forge=MemoryForge(),
        catalog=catalog,
        delay=0.0,
    )
    consumer = next(c for c in harness.consumers if c.step == "unblock")
    return consumer.behavior


def test_unblock_behavior_is_wired_with_the_served_workflows(tmp_path):
    """Invariant 42: the workflow is the authoritative declaration of a step's
    outcome vocabulary, hints and description — `UnblockPrBehavior` can only
    read it if `build()` hands it the repository, exactly as it does for
    `ClaudeCliBehavior`."""
    behavior = _unblock_behavior(
        tmp_path, spec=AgentSpec(name="unblock", prompt="p")
    )

    assert behavior._workflows is not None
    assert "resolver" in behavior._workflows.names()


def test_unblock_behavior_honours_a_per_persona_timeout(tmp_path):
    """`agents/unblock.json` may set its own `timeout`, the same way every
    other persona's is honoured on the `ClaudeCliBehavior` branch."""
    behavior = _unblock_behavior(
        tmp_path, spec=AgentSpec(name="unblock", prompt="p", timeout=123.0)
    )

    assert behavior._timeout == 123.0


def test_build_without_sources_has_no_pollers(tmp_path):
    """Backward compatibility: default sources=[] → no poller, behavior as before."""
    seed(tmp_path)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert harness.pollers == []


async def test_run_drives_a_task_all_the_way_to_done(tmp_path):
    seed(tmp_path)
    events = MemoryEventSink()
    harness = build(
        tmp_path,
        "default",
        events=events,
        delay=0.0,
        request_changes_once_at="review",
    )
    task = Task(id="tsk_1", workflow_template="default", created="2026-07-19T10:00:00Z")
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(400):
        await asyncio.sleep(0.01)
        if (tmp_path / "done" / "tsk_1.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    assert (tmp_path / "done" / "tsk_1.json").exists()
    finished = Task.from_dict(json.loads((tmp_path / "done" / "tsk_1.json").read_text()))
    visited = [entry.to_step for entry in finished.history if entry.actor == "dispatcher"]
    assert visited == ["plan", "review", "plan", "review", "end"]


async def test_run_stops_promptly_even_on_long_intervals(tmp_path):
    """Every loop idles on `stop`, never on a blind `sleep(interval)`.

    The intervals here are longer than any test could wait for, and none of them
    is ever actually awaited: each loop ticks once over empty queues, then parks
    on `asyncio.wait_for(stop.wait(), timeout=interval)`. `stop.set()` wakes all
    of them at once, so `run()` returns immediately. Restore a plain
    `asyncio.sleep(interval)` in `_tick_loop` and this test hangs for ten minutes
    instead — which is what `cli.serve()`'s `finally: stop.set(); await
    asyncio.gather(...)` does under launchd, well past its SIGKILL deadline.
    """
    seed(tmp_path)
    harness = build(tmp_path, "default", events=MemoryEventSink(), delay=0.0)

    stop = asyncio.Event()
    runner = asyncio.create_task(
        harness.run(
            poll_interval=600,
            source_interval=600,
            reconcile_interval=600,
            stop=stop,
        )
    )
    # Let every loop tick once and park on its idle wait. A bare `sleep(0)` is
    # not enough — it only gets `run()` as far as its `gather`, before the loop
    # bodies have run at all, and every loop would then exit on its `while not
    # stop.is_set()` guard whatever it idles on. One real timer tick drains all
    # ready callbacks, so each loop is genuinely waiting when `stop` is set.
    await asyncio.sleep(0.01)
    stop.set()

    await asyncio.wait_for(runner, timeout=1)


def test_build_without_a_workflow_name_has_no_workflow(tmp_path):
    """FR-6/FR-7: a --no-workflow harness (no workflow name given) still
    builds — queue discovery no longer depends on a mandatory workflow."""
    layout = HarnessLayout(tmp_path)
    layout.agents.mkdir(parents=True, exist_ok=True)
    (layout.agents / "triage.json").write_text(json.dumps({"prompt": "x"}))
    catalog = MemoryAgentCatalog({"triage": AgentSpec(name="triage", prompt="x")})

    harness = build(tmp_path, catalog=catalog, events=MemoryEventSink())

    assert harness.workflows == {}
    # A workflow-less harness has no workflow tab, so its catalog agents render
    # as columns under the sole UNKNOWN tab (which is kept even while empty
    # because it is the whole board).
    board = harness.projection.snapshot()
    assert board.workflow(UNKNOWN_WORKFLOW).column("triage") is not None
    assert (tmp_path / "queues" / "triage").is_dir()


def test_build_discovers_queues_as_union_of_workflows_and_catalog(tmp_path):
    """FR-7: with two workflow files and a catalog with an extra standalone
    agent, the queue set is the union of all three sources, with no
    duplicates."""
    layout = seed(tmp_path)
    hotfix = {
        "name": "hotfix",
        "start": "patch",
        "transitions": [{"from": "patch", "on": "done", "to": "end"}],
    }
    (layout.workflows / "hotfix.json").write_text(json.dumps(hotfix))
    # A catalog with a spec for every discovered step: when a catalog is
    # wired, every step resolves through it (fail fast on a missing spec),
    # so this must cover the union, not just the standalone extra agent.
    catalog = MemoryAgentCatalog(
        {
            name: AgentSpec(name=name, prompt="x")
            for name in ("plan", "review", "patch", "triage")
        }
    )

    harness = build(tmp_path, "default", catalog=catalog, events=MemoryEventSink())

    assert set(harness._step_queues) == {"plan", "review", "patch", "triage"}


def test_build_with_one_workflow_and_no_catalog_is_unchanged(tmp_path):
    """FR-7: the union degenerates to exactly today's shape when there is one
    workflow and no catalog — a strict backward-compatible generalization."""
    seed(tmp_path)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert set(harness._step_queues) == {"plan", "review"}


def test_behavior_for_uses_spec_timeout_override_else_agent_timeout(tmp_path):
    seed(tmp_path)
    catalog = MemoryAgentCatalog(
        {
            "plan": AgentSpec(name="plan", prompt="p", timeout=45.0),
            "review": AgentSpec(name="review", prompt="r"),
        }
    )

    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        catalog=catalog,
        agent_timeout=900.0,
    )

    by_actor = {consumer.actor: consumer for consumer in harness.consumers}
    assert by_actor["consumer:plan"]._behavior._timeout == 45.0
    assert by_actor["consumer:review"]._behavior._timeout == 900.0


class ConcurrencyProbeBehavior(ConsumerBehavior):
    """Records, per step, how many `run()` calls were in flight at once.

    `task.status` is the step the task is currently queued under (the
    dispatcher stamps it before handing the task to that step's queue), so a
    single shared instance can distinguish concurrency per step even though
    `build()` wires one behavior instance across every non-landing consumer.
    """

    def __init__(self, hold: float) -> None:
        self._hold = hold
        self.current: dict[str, int] = {}
        self.max_seen: dict[str, int] = {}

    async def run(self, task: Task) -> BehaviorResult:
        step = task.status
        self.current[step] = self.current.get(step, 0) + 1
        self.max_seen[step] = max(self.max_seen.get(step, 0), self.current[step])
        await asyncio.sleep(self._hold)
        self.current[step] -= 1
        return BehaviorResult(DONE, summary="probed")


async def test_max_parallel_bounds_concurrent_runs_per_step(tmp_path):
    """FR-3: a step's configured maxParallel is the ceiling on concurrent
    `ConsumerBehavior.run()` calls for that step, while a step with no
    override never exceeds the default of 1 — in the same harness run."""
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    definition = {
        "name": "default",
        "start": "plan",
        "transitions": [
            {"from": "plan", "on": "done", "to": "review"},
            {"from": "review", "on": "done", "to": "end"},
        ],
        "maxParallel": {"review": 2},
    }
    (layout.workflows / "default.json").write_text(json.dumps(definition))

    probe = ConcurrencyProbeBehavior(hold=0.05)
    harness = build(tmp_path, "default", events=MemoryEventSink(), behavior=probe)

    for i in range(3):
        plan_task = Task(
            id=f"tsk_plan_{i}",
            workflow_template="default",
            created="2026-07-21T10:00:00Z",
            status="plan",
        )
        (tmp_path / "queues" / "plan" / f"tsk_plan_{i}.json").write_text(
            json.dumps(plan_task.to_dict())
        )
        review_task = Task(
            id=f"tsk_review_{i}",
            workflow_template="default",
            created="2026-07-21T10:00:00Z",
            status="review",
        )
        (tmp_path / "queues" / "review" / f"tsk_review_{i}.json").write_text(
            json.dumps(review_task.to_dict())
        )

    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(400):
        await asyncio.sleep(0.01)
        if len(list((tmp_path / "done").glob("tsk_*.json"))) >= 6:
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    assert probe.max_seen["review"] == 2
    assert probe.max_seen["plan"] == 1


def test_recover_returns_stranded_tasks(tmp_path):
    seed(tmp_path)
    harness = build(tmp_path, "default", events=MemoryEventSink())
    stranded = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        lock_id="lck_1",
    )
    (tmp_path / "tasks" / ".processing" / "tsk_1.json").write_text(
        json.dumps(stranded.to_dict())
    )

    assert harness.recover() == 1
    assert (tmp_path / "tasks" / "tsk_1.json").exists()
    revived = Task.from_dict(json.loads((tmp_path / "tasks" / "tsk_1.json").read_text()))
    assert revived.lock_id is None


def test_recover_returns_a_task_stranded_between_pr_watcher_claim_and_transfer(tmp_path):
    """PrWatcher claims out of done/ before transferring to archived/ — a crash
    between those two calls must not silently lose the task. Before the fix,
    recover() only walked [inbox, *step_queues.values()], leaving done/.processing/
    unrecovered and the task invisible to both done.list() and hydrate()."""
    seed(tmp_path)
    harness = build(tmp_path, "default", events=MemoryEventSink())
    stranded = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="end",
        lock_id="lck_1",
        data={"pr": {"number": 1, "url": "u", "branch": "harness/tsk_1"}},
    )
    (tmp_path / "done" / ".processing" / "tsk_1.json").write_text(
        json.dumps(stranded.to_dict())
    )

    assert harness.recover() == 1
    assert (tmp_path / "done" / "tsk_1.json").exists()
    revived = Task.from_dict(json.loads((tmp_path / "done" / "tsk_1.json").read_text()))
    assert revived.lock_id is None


async def test_pr_watcher_archives_a_resolved_task_end_to_end(tmp_path):
    seed(tmp_path)
    forge = FakeForge(tmp_path / "forge")
    events = MemoryEventSink()
    # Pinned to the seeded task's own `created` instant (`FakeClock`'s default)
    # so the always-on retention sweep sees a freshly-settled task and leaves it
    # alone. Without this the harness runs on the real `SystemClock`, the 2026-07-19
    # task reads as long past the retention window, and the *retention* reconciler
    # archives it — satisfying every assertion below and leaving the PR watcher,
    # the actual subject, untested. The sweep runs on its loop's first tick, before
    # any interval elapses, so no `reconcile_interval` can disarm it.
    harness = build(
        tmp_path,
        "default",
        events=events,
        forge=forge,
        delay=0.0,
        clock=FakeClock(),
    )
    landed = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="end",
    )
    pull = forge.open_pull_request(
        landed, branch="harness/tsk_1", title="T", body="B"
    )
    landed = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="end",
        data={"pr": {"number": pull.number, "url": pull.url, "branch": pull.branch}},
    )
    (tmp_path / "done" / "tsk_1.json").write_text(json.dumps(landed.to_dict()))
    forge.close_pull_request("harness/tsk_1", merged=True)

    stop = asyncio.Event()
    runner = asyncio.create_task(
        harness.run(poll_interval=0.01, pr_poll_interval=0.01, stop=stop)
    )
    for _ in range(400):
        await asyncio.sleep(0.01)
        if (tmp_path / "archived" / "tsk_1.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    assert (tmp_path / "archived" / "tsk_1.json").exists()
    assert not (tmp_path / "done" / "tsk_1.json").exists()
    archived = Task.from_dict(json.loads((tmp_path / "archived" / "tsk_1.json").read_text()))
    assert archived.status == ARCHIVED
    # It was the *PR watcher* that archived it, not the always-on retention
    # sweep — the other reconciler that lands a task in exactly this state.
    assert archived.history[-1].actor == "pr_watcher"
    assert harness.projection.get("tsk_1") is not None
    assert all(
        task.id != "tsk_1"
        for tab in harness.projection.snapshot().workflows
        for column in tab.columns
        for task in column.tasks
    )


async def test_pr_watcher_loop_does_not_run_when_interval_is_zero(tmp_path):
    seed(tmp_path)
    forge = FakeForge(tmp_path / "forge")
    # Pinned to the task's own `created` instant (`FakeClock`'s default) so the
    # always-on retention sweep — which ticks once at startup, whatever the
    # interval — sees a freshly-settled task, not a real-world multi-day-old
    # one, and leaves it alone; only the PR watcher's own zero-interval gate is
    # under test here.
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        forge=forge,
        delay=0.0,
        clock=FakeClock(),
    )
    landed = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="end",
    )
    pull = forge.open_pull_request(landed, branch="harness/tsk_1", title="T", body="B")
    landed = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="end",
        data={"pr": {"number": pull.number, "url": pull.url, "branch": pull.branch}},
    )
    (tmp_path / "done" / "tsk_1.json").write_text(json.dumps(landed.to_dict()))
    forge.close_pull_request("harness/tsk_1", merged=True)

    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    # pr_poll_interval defaults to 0.0 (disabled) — the task stays in done/.
    assert (tmp_path / "done" / "tsk_1.json").exists()
    assert not (tmp_path / "archived" / "tsk_1.json").exists()


async def test_projection_is_hydrated_from_queues_at_start(tmp_path):
    seed(tmp_path)
    harness = build(tmp_path, "default", delay=0.0)
    waiting = Task(
        id="tsk_9",
        workflow_template="default",
        created="2026-07-19T09:00:00Z",
        status="review",
    )
    (tmp_path / "queues" / "review" / "tsk_9.json").write_text(
        json.dumps(waiting.to_dict())
    )

    stop = asyncio.Event()
    stop.set()
    await harness.run(poll_interval=0.01, stop=stop)

    assert harness.projection.get("tsk_9") is not None
    assert harness.projection.snapshot().workflow("default").column("review").tasks[0].id == "tsk_9"


def test_seed_pollers_collects_from_every_queue(tmp_path):
    # A task for issue #1 already sits in `done/` (it ran to completion before a
    # restart). Seeding must find it there so the source can't re-ingest #1.
    seed(tmp_path)
    source = ReplaySource(issue=1)
    harness = build(tmp_path, "default", events=MemoryEventSink(), sources=[source])
    done_task = Task(
        id="tsk_done",
        workflow_template="default",
        created="2026-07-20T09:00:00Z",
        dedup_key="github:o/r:1",
        data={"source": {"kind": "github", "repo": "o/r", "issue": 1}},
    )
    (tmp_path / "done" / "tsk_done.json").write_text(json.dumps(done_task.to_dict()))

    harness._seed_pollers()

    # The source would hand back issue #1, but it is already on disk → skipped.
    assert harness.pollers[0].tick() is False
    assert list((tmp_path / "tasks").glob("tsk_*.json")) == []


def test_build_without_merge_checker_has_no_reconciler(tmp_path):
    """Without a merge_checker there is no MergeReconciler. The `archived/` queue
    still exists — it is shared with the always-built PrWatcher (#44), which is
    what drops resolved tasks off the board when its poll loop is enabled."""
    seed(tmp_path)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert harness.reconciler is None
    assert harness.archived is not None


def test_build_with_merge_checker_wires_archived_queue_and_reconciler(tmp_path):
    seed(tmp_path)

    harness = build(
        tmp_path, "default", events=MemoryEventSink(), merge_checker=FakeMergeChecker()
    )

    assert harness.archived is not None
    assert harness.reconciler is not None
    assert (tmp_path / "archived").is_dir()


async def test_run_archives_a_done_task_once_its_pr_is_merged(tmp_path):
    seed(tmp_path)
    checker = FakeMergeChecker()
    checker.merged.add(("o/r", 1))
    events = MemoryEventSink()
    # Pinned to the seeded task's own `created` instant (`FakeClock`'s default)
    # so the always-on retention sweep sees a freshly-settled task and leaves it
    # alone. On the real `SystemClock` this history-less 2026-07-19 task reads as
    # ~11 days past the retention window, so the *retention* sweep archived it on
    # its loop's first tick — satisfying every assertion below with the merge
    # reconciler never involved at all.
    harness = build(
        tmp_path,
        "default",
        events=events,
        delay=0.0,
        merge_checker=checker,
        clock=FakeClock(),
    )
    done_task = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="end",
        data={"pr": {"repo": "o/r", "number": 1, "url": "u", "branch": "b"}},
    )
    (tmp_path / "done" / "tsk_1.json").write_text(json.dumps(done_task.to_dict()))

    stop = asyncio.Event()
    runner = asyncio.create_task(
        harness.run(poll_interval=0.01, reconcile_interval=0.01, stop=stop)
    )
    for _ in range(400):
        await asyncio.sleep(0.01)
        if (tmp_path / "archived" / "tsk_1.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    assert (tmp_path / "archived" / "tsk_1.json").exists()
    assert not (tmp_path / "done" / "tsk_1.json").exists()
    archived = Task.from_dict(
        json.loads((tmp_path / "archived" / "tsk_1.json").read_text())
    )
    # It was the *merge reconciler* that archived it, not the always-on retention
    # sweep — the other reconciler that lands a task in exactly this state.
    assert archived.history[-1].actor == "merge_reconciler"
    assert harness.projection.get("tsk_1") is not None
    assert all(
        task.id != "tsk_1"
        for tab in harness.projection.snapshot().workflows
        for column in tab.columns
        for task in column.tasks
    )


def test_build_without_issue_checker_has_no_issue_reconciler(tmp_path):
    seed(tmp_path)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert harness.issue_reconciler is None


def test_build_with_issue_checker_wires_the_issue_reconciler(tmp_path):
    seed(tmp_path)

    harness = build(
        tmp_path, "default", events=MemoryEventSink(), issue_checker=FakeIssueChecker()
    )

    assert harness.issue_reconciler is not None
    assert (tmp_path / "archived").is_dir()


async def test_run_archives_a_task_once_its_source_issue_is_closed(tmp_path):
    seed(tmp_path)
    checker = FakeIssueChecker()
    checker.closed.add(("o/r", 1))
    events = MemoryEventSink()
    harness = build(
        tmp_path, "default", events=events, delay=0.0, issue_checker=checker
    )
    # A task sitting mid-workflow (in the `plan` step queue) whose issue was
    # closed out from under it — not a done/landed task.
    stale = Task(
        id="tsk_stale",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="plan",
        data={"source": {"kind": "github", "repo": "o/r", "issue": 1}},
    )
    (tmp_path / "queues" / "plan" / "tsk_stale.json").write_text(
        json.dumps(stale.to_dict())
    )

    stop = asyncio.Event()
    runner = asyncio.create_task(
        harness.run(poll_interval=0.01, reconcile_interval=0.01, stop=stop)
    )
    for _ in range(400):
        await asyncio.sleep(0.01)
        if (tmp_path / "archived" / "tsk_stale.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    assert (tmp_path / "archived" / "tsk_stale.json").exists()
    # Off the board, still gettable by id (the `archived/` contract).
    assert harness.projection.get("tsk_stale") is not None
    assert all(
        task.id != "tsk_stale"
        for tab in harness.projection.snapshot().workflows
        for column in tab.columns
        for task in column.tasks
    )


def test_recover_returns_stranded_done_tasks(tmp_path):
    """A crash between MergeReconciler's claim and transfer must not lose the
    task — recover() includes `done` unconditionally, even without a
    reconciler wired this run."""
    seed(tmp_path)
    harness = build(tmp_path, "default", events=MemoryEventSink())
    stranded = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="end",
        lock_id="lck_1",
        data={"pr": {"repo": "o/r", "number": 1, "url": "u", "branch": "b"}},
    )
    (tmp_path / "done" / ".processing" / "tsk_1.json").write_text(
        json.dumps(stranded.to_dict())
    )

    assert harness.recover() == 1
    assert (tmp_path / "done" / "tsk_1.json").exists()
    revived = Task.from_dict(json.loads((tmp_path / "done" / "tsk_1.json").read_text()))
    assert revived.lock_id is None


async def test_stranded_task_survives_into_the_projection(tmp_path):
    seed(tmp_path)
    harness = build(tmp_path, "default", delay=0.0)
    stranded = Task(
        id="tsk_8",
        workflow_template="default",
        created="2026-07-19T09:00:00Z",
        status="plan",
        lock_id="lck_1",
    )
    (tmp_path / "queues" / "plan" / ".processing" / "tsk_8.json").write_text(
        json.dumps(stranded.to_dict())
    )

    stop = asyncio.Event()
    stop.set()
    await harness.run(poll_interval=0.01, stop=stop)

    assert harness.projection.get("tsk_8") is not None


HOTFIX_DEFINITION = {
    "name": "hotfix",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "end"},
    ],
}


def seed_two_workflows(tmp_path):
    """`default` (plan -> review) and `hotfix` (plan -> review), sharing the
    `plan` and `review` step names."""
    layout = seed(tmp_path)
    (layout.workflows / "hotfix.json").write_text(json.dumps(HOTFIX_DEFINITION))
    return layout


def test_build_with_two_workflows_unions_step_queues(tmp_path):
    seed_two_workflows(tmp_path)

    harness = build(tmp_path, ["default", "hotfix"], events=MemoryEventSink())

    assert set(harness.workflows) == {"default", "hotfix"}
    assert sorted(harness._step_queues) == ["plan", "review"]
    assert len(harness.consumers) == 2


async def test_two_served_workflows_route_tasks_to_their_own_steps_via_shared_queue(
    tmp_path,
):
    seed_two_workflows(tmp_path)
    harness = build(tmp_path, ["default", "hotfix"], events=MemoryEventSink(), delay=0.0)
    default_task = Task(
        id="tsk_default", workflow_template="default", created="2026-07-19T10:00:00Z"
    )
    hotfix_task = Task(
        id="tsk_hotfix", workflow_template="hotfix", created="2026-07-19T10:00:00Z"
    )
    (tmp_path / "tasks" / "tsk_default.json").write_text(json.dumps(default_task.to_dict()))
    (tmp_path / "tasks" / "tsk_hotfix.json").write_text(json.dumps(hotfix_task.to_dict()))

    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(400):
        await asyncio.sleep(0.01)
        if (tmp_path / "done" / "tsk_default.json").exists() and (
            tmp_path / "done" / "tsk_hotfix.json"
        ).exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    assert (tmp_path / "done" / "tsk_default.json").exists()
    assert (tmp_path / "done" / "tsk_hotfix.json").exists()


def test_task_for_unserved_but_existing_workflow_fails_with_clear_message(tmp_path):
    """`hotfix` is a valid definition on disk, but this harness only serves
    `default` — the task must fail fast with a message naming the unserved
    workflow, not the generic "no queue" message."""
    seed_two_workflows(tmp_path)
    harness = build(tmp_path, "default", events=MemoryEventSink(), delay=0.0)
    task = Task(
        id="tsk_1", workflow_template="hotfix", created="2026-07-19T10:00:00Z"
    )
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    assert harness.dispatcher.tick() is True

    failed = Task.from_dict(
        json.loads((tmp_path / "failed" / "tsk_1.json").read_text())
    )
    assert "hotfix" in failed.history[-1].reason
    assert "not served" in failed.history[-1].reason
    assert "no queue" not in failed.history[-1].reason


def test_task_for_nonexistent_workflow_still_fails_as_before(tmp_path):
    seed_two_workflows(tmp_path)
    harness = build(tmp_path, "default", events=MemoryEventSink(), delay=0.0)
    task = Task(
        id="tsk_1", workflow_template="does-not-exist", created="2026-07-19T10:00:00Z"
    )
    (tmp_path / "tasks" / "tsk_1.json").write_text(json.dumps(task.to_dict()))

    assert harness.dispatcher.tick() is True

    failed = Task.from_dict(
        json.loads((tmp_path / "failed" / "tsk_1.json").read_text())
    )
    assert "does-not-exist" in failed.history[-1].reason


PUBLISH_DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "publish"},
        {"from": "publish", "on": "done", "to": "end"},
    ],
    "finishers": {"publish": "open-pr"},
}


def seed_definition(tmp_path, definition, name="default"):
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / f"{name}.json").write_text(json.dumps(definition))
    return layout


class RecordingFinisher(ConsumerBehavior):
    """A minimal caller-supplied finisher: records the tasks it finished."""

    def __init__(self) -> None:
        self.finished: list[str] = []

    async def run(self, task: Task) -> BehaviorResult:
        self.finished.append(task.id)
        return BehaviorResult(DONE, summary="recorded")


async def test_custom_step_bound_to_open_pr_lands_through_the_forge(tmp_path):
    """ADR-0016 e2e: a workflow with no "land" step at all binds its own
    "publish" step to the "open-pr" kind — the forge opens the PR from that
    step, with no name-keyed branch involved."""
    seed_definition(tmp_path, PUBLISH_DEFINITION)
    forge = MemoryForge()
    harness = build(
        tmp_path, "default", events=MemoryEventSink(), forge=forge, delay=0.0
    )
    task = Task(
        id="tsk_pub",
        workflow_template="default",
        created="2026-07-23T10:00:00Z",
        repository="app",
        worktree="/work/tsk_pub",
        data={"title": "publish it"},
    )
    (tmp_path / "tasks" / "tsk_pub.json").write_text(json.dumps(task.to_dict()))

    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(400):
        await asyncio.sleep(0.01)
        if (tmp_path / "done" / "tsk_pub.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    assert (tmp_path / "done" / "tsk_pub.json").exists()
    assert len(forge.opened) == 1
    assert forge.opened[0].branch == "harness/tsk_pub"


def test_workflow_without_finishers_key_still_binds_land_to_landing(tmp_path):
    """Backward compatibility (ADR-0016): with no `finishers` key, the map
    defaults `landing_step` to "open-pr", so a pre-feature workflow's `land`
    consumer still carries the LandingBehavior — exactly as before."""
    definition = {
        "name": "default",
        "start": "plan",
        "transitions": [
            {"from": "plan", "on": "done", "to": "land"},
            {"from": "land", "on": "done", "to": "end"},
        ],
    }
    seed_definition(tmp_path, definition)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    by_actor = {consumer.actor: consumer for consumer in harness.consumers}
    assert isinstance(by_actor["consumer:land"]._behavior, LandingBehavior)


def test_build_with_unknown_finisher_kind_fails_at_build(tmp_path):
    definition = {**PUBLISH_DEFINITION, "finishers": {"publish": "call-api"}}
    seed_definition(tmp_path, definition)

    with pytest.raises(ValueError, match="call-api"):
        build(tmp_path, "default", events=MemoryEventSink())


def test_build_with_conflicting_finisher_bindings_fails_at_build(tmp_path):
    """Two served workflows share the `publish` step queue, so binding it to
    two different kinds is a wiring contradiction — build must refuse, not
    let queue order decide which finisher wins."""
    layout = seed_definition(tmp_path, PUBLISH_DEFINITION)
    other = {
        **PUBLISH_DEFINITION,
        "name": "other",
        "finishers": {"publish": "record"},
    }
    (layout.workflows / "other.json").write_text(json.dumps(other))

    with pytest.raises(ValueError, match="conflicting"):
        build(
            tmp_path,
            ["default", "other"],
            events=MemoryEventSink(),
            finishers={"record": lambda step, config, inner: RecordingFinisher()},
        )


def test_caller_supplied_finisher_registry_entry_is_used(tmp_path):
    definition = {**PUBLISH_DEFINITION, "finishers": {"publish": "record"}}
    seed_definition(tmp_path, definition)
    recorder = RecordingFinisher()

    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        finishers={"record": lambda step, config, inner: recorder},
    )

    by_actor = {consumer.actor: consumer for consumer in harness.consumers}
    assert by_actor["consumer:publish"]._behavior is recorder


# --- ADR-0022: pre-flight finisher-binding validation (cli._run's served-set
# filter, before build()'s own eager fail-fast) -------------------------------


def _one_step_workflow(step: str, binding: FinisherBinding) -> Workflow:
    return Workflow(
        name="w",
        start=step,
        transitions=(Transition(from_step=step, on="done", to_step=END),),
        finishers={step: binding},
    )


def test_validate_workflow_finishers_passes_a_wirable_binding():
    workflow = _one_step_workflow(
        "file-issue", FinisherBinding(kind="open-issue", config={"label": "x"})
    )
    calls = []

    def factory(step, config, inner):
        assert config["label"] == "x"
        calls.append((step, config))
        return RecordingFinisher()

    validate_workflow_finishers(workflow, {"open-issue": factory})
    # A call-count assertion, not just an in-factory one: without it this test
    # stays green even if `validate_workflow_finishers` became a no-op for a
    # known kind and never called the factory at all.
    assert calls == [("file-issue", {"label": "x"})]


def test_validate_workflow_finishers_raises_when_the_factory_raises():
    """Mirrors the reference-install bug: the old string form `"open-issue"`
    parses to an empty config, and the real factory raises over the missing
    `label` — this must propagate, not be swallowed. It must also NOT be an
    `UnknownFinisherKind` — `open-issue` is a perfectly well-known kind here,
    only its config is incomplete, and `cli._validate_served_workflows` tells
    the two shapes apart by this exact type, not by matching on message text
    (warn-and-drop for this one, fail-the-whole-run for the other)."""
    workflow = _one_step_workflow("file-issue", FinisherBinding(kind="open-issue"))

    def factory(step, config, inner):
        if not config.get("label"):
            raise ValueError(f"step {step!r} has no label")
        return RecordingFinisher()

    with pytest.raises(ValueError, match="file-issue") as excinfo:
        validate_workflow_finishers(workflow, {"open-issue": factory})
    assert not isinstance(excinfo.value, UnknownFinisherKind)


def test_validate_workflow_finishers_raises_on_unknown_kind():
    """A distinct type (`UnknownFinisherKind`, still a `ValueError` so any
    caller matching on the base type keeps working) — an unknown kind is a
    value that's *set and wrong* (invariant: fails fast), unlike a known kind
    with incomplete config (invariant: only that binding is unwirable). The
    enumeration mirrors `build()`'s own diagnostic (`(known: ...)`), the hint
    that tells an operator a kind is token-gated rather than misspelled."""
    workflow = _one_step_workflow("publish", FinisherBinding(kind="call-api"))

    with pytest.raises(UnknownFinisherKind, match="call-api") as excinfo:
        validate_workflow_finishers(workflow, {"open-issue": lambda *a: None})
    assert "known: open-issue, open-pr, verify" in str(excinfo.value)


def test_validate_workflow_finishers_never_raises_for_the_builtin_kinds():
    """`open-pr`/`verify` are always wirable — no factory needs to be
    supplied for them, exactly as `build()`'s own default registry needs
    none from the caller."""
    workflow = Workflow(
        name="w",
        start="land",
        transitions=(Transition(from_step="land", on="done", to_step=END),),
        finishers={"land": FinisherBinding(kind="open-pr")},
    )

    validate_workflow_finishers(workflow, None)


def test_validate_workflow_finishers_calls_inner_for_a_wrap_shaped_factory():
    """A wrap-shaped finisher (like `label-issue`) calls its `inner` thunk
    eagerly; the thunk this validator hands it is inert (returns `None`), so
    the factory must not choke on that to prove its own kind/config
    resolution works."""
    workflow = _one_step_workflow("triage", FinisherBinding(kind="label-issue"))
    seen = {}

    def factory(step, config, inner):
        seen["inner"] = inner()
        return RecordingFinisher()

    validate_workflow_finishers(workflow, {"label-issue": factory})
    assert seen["inner"] is None


def test_validate_workflow_finishers_raises_unknown_kind_regardless_of_binding_order():
    """Reproduces the reviewer's exact finding: a single loop that did the
    unknown-kind lookup and the factory invocation together let whichever
    binding a dict iterates first win, so "an unknown kind is fatal" silently
    depended on JSON key order. Two bindings in the *losing* order — the
    config-shaped failure (`open-issue` with no `label`, a known kind whose
    factory raises a plain `ValueError`) iterated before the unknown kind —
    used to let that `ValueError` escape first and mask the unknown kind
    entirely. This must still raise `UnknownFinisherKind`, not the earlier
    `ValueError`. A test in the winning order alone (unknown kind first)
    would not gate a regression back to the single-loop shape."""
    workflow = Workflow(
        name="w",
        start="a",
        transitions=(
            Transition(from_step="a", on="done", to_step="b"),
            Transition(from_step="b", on="done", to_step=END),
        ),
        finishers={
            # Losing order: the config-shaped failure comes first.
            "a": FinisherBinding(kind="open-issue"),
            "b": FinisherBinding(kind="call-a-webhook"),
        },
    )

    def open_issue_factory(step, config, inner):
        if not config.get("label"):
            raise ValueError(f"step {step!r} has no label")
        return RecordingFinisher()

    with pytest.raises(UnknownFinisherKind, match="call-a-webhook"):
        validate_workflow_finishers(workflow, {"open-issue": open_issue_factory})


# --- ADR-0018: process compilation moved inside build() ---------------------


def _write_process(tmp_path, name, body):
    processes = tmp_path / "processes"
    processes.mkdir(parents=True, exist_ok=True)
    (processes / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


def test_build_compiles_processes_root_targeting_a_served_workflow_by_name(tmp_path):
    """architecture-02 §2.2's fix, made explicit and readable: `known_workflows`
    passed to `FilesystemProcessRepository.build()` must be the served
    *workflow* name set — a `{"workflow": "default"}` target names the
    workflow itself, which is not one of DEFINITION's own step names
    (plan/development/review/land). Before the fix this process would fail
    `ProcessValidationError` at every `build()` call."""
    seed_definition(tmp_path, DEFINITION)
    _write_process(
        tmp_path,
        "nightly",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "default"},
            "sink": {"kind": "none"},
        },
    )

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert any(
        getattr(poller, "_source", None) is not None for poller in harness.pollers
    )
    assert len(harness.pollers) == 1


def test_build_rejects_a_step_target_naming_a_served_workflow_name(tmp_path):
    """The bug this change fixes, reproduced at the `build()` boundary: a
    `{"step": "default"}` target where "default" is a served workflow's
    *name*, not a queued step, used to pass the old merged `known_targets`
    check and only fail later at dispatch (`step 'default' has no queue`).
    It must now fail fast at `build()`, before any task is ever produced."""
    from harness.drivers.fs_processes import ProcessValidationError

    seed_definition(tmp_path, DEFINITION)
    _write_process(
        tmp_path,
        "nightly",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"step": "default"},
            "sink": {"kind": "none"},
        },
    )

    with pytest.raises(ProcessValidationError):
        build(tmp_path, "default", events=MemoryEventSink())


def test_build_processes_root_defaults_to_layout_processes(tmp_path):
    """No `processes/` directory at all → no pollers, exactly as before this
    capability existed (backward compatible)."""
    seed_definition(tmp_path, DEFINITION)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert harness.pollers == []


def test_build_extra_checks_merge_over_builtin_checks(tmp_path):
    """`extra_checks` (the dependency-bag shape `github-issues`/
    `github-unhealthy-prs` use, wired from `cli.py`) is merged into the checks
    dict `build()` uses to compile `processes/*.json` — alongside its own
    unconditional `"failed-tasks"` entry, never replacing it."""
    from harness.drivers.checks import AlwaysCheck

    seed_definition(tmp_path, DEFINITION)
    _write_process(
        tmp_path,
        "custom",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "custom-check"},
            "target": {"workflow": "default"},
            "sink": {"kind": "none"},
        },
    )

    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        extra_checks={"custom-check": lambda params: AlwaysCheck()},
    )

    assert len(harness.pollers) == 1


def test_build_exposes_the_effective_process_check_registry(tmp_path):
    """`harness.process_checks` is the very registry `build()` compiled
    `processes/*.json` against — built-ins, `extra_checks` and the internal
    `failed-tasks` — so `serve()` can hand it to `FilesystemProcessAdmin` and
    the dashboard's process form offers and validates exactly what this run
    accepts, never a narrower admin-only subset."""
    from harness.drivers.checks import BUILTIN_CHECKS, AlwaysCheck

    seed_definition(tmp_path, DEFINITION)

    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        extra_checks={"custom-check": lambda params: AlwaysCheck()},
    )

    assert set(BUILTIN_CHECKS) <= set(harness.process_checks)
    assert "custom-check" in harness.process_checks
    assert "failed-tasks" in harness.process_checks


def test_build_wires_failed_tasks_as_a_defined_action_with_a_spec(tmp_path):
    """`failed-tasks` is wired as a full action definition, not a bare lambda:
    it carries a declarative `CheckSpec` (label, no params), so the process
    form renders it like any other action instead of falling back to raw JSON."""
    from harness.ports.triggers import check_spec_of

    seed_definition(tmp_path, DEFINITION)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    spec = check_spec_of("failed-tasks", harness.process_checks["failed-tasks"])
    assert spec.name == "failed-tasks"
    assert spec.label == "Failed tasks"
    assert spec.params == ()


def test_build_unknown_check_still_fails_fast(tmp_path):
    from harness.drivers.fs_processes import ProcessValidationError

    seed_definition(tmp_path, DEFINITION)
    _write_process(
        tmp_path,
        "bogus",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "does-not-exist"},
            "target": {"workflow": "default"},
        },
    )

    with pytest.raises(ProcessValidationError):
        build(tmp_path, "default", events=MemoryEventSink())


def test_build_processes_root_parameter_points_at_a_different_directory(tmp_path):
    seed_definition(tmp_path, DEFINITION)
    _write_process(
        tmp_path / "elsewhere",
        "nightly",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "default"},
            "sink": {"kind": "none"},
        },
    )

    # The default location (tmp_path/"processes") has nothing in it.
    default_location = build(tmp_path, "default", events=MemoryEventSink())
    assert default_location.pollers == []

    redirected = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        processes_root=tmp_path / "elsewhere" / "processes",
    )
    assert len(redirected.pollers) == 1


def test_build_repository_registry_validates_the_process_repository_field(tmp_path):
    """`repository_registry=` computes `known_repositories` for the internal
    `FilesystemProcessRepository.build()` call — a process naming a repository
    outside the registry fails fast, matching `known_steps`/`known_workflows`'s
    shape."""
    from pathlib import Path

    from harness.drivers.fs_processes import ProcessValidationError

    seed_definition(tmp_path, DEFINITION)
    _write_process(
        tmp_path,
        "nightly",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "default"},
            "repository": "ghost",
        },
    )
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})

    with pytest.raises(ProcessValidationError):
        build(tmp_path, "default", events=MemoryEventSink(), repository_registry=registry)


def test_build_repository_registry_omitted_is_lenient(tmp_path):
    """No `repository_registry` at all (the default) keeps behaving exactly as
    before — a process naming any repository compiles, unvalidated."""
    seed_definition(tmp_path, DEFINITION)
    _write_process(
        tmp_path,
        "nightly",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "default"},
            "repository": "whatever",
        },
    )

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert len(harness.pollers) == 1


def test_build_threads_repository_registry_names_into_the_board(tmp_path):
    """The board's filter-bar repository options come from the registry, sorted
    — not from an ad-hoc scan of whatever tasks happen to be on the board."""
    from pathlib import Path

    seed_definition(tmp_path, DEFINITION)
    registry = MemoryRepositoryRegistry(
        {"harness_v2": Path("/repos/harness_v2"), "another": Path("/repos/another")}
    )

    harness = build(tmp_path, "default", events=MemoryEventSink(), repository_registry=registry)

    assert harness.projection.snapshot().repository_names == ("another", "harness_v2")


def test_build_without_repository_registry_leaves_board_repository_names_empty(tmp_path):
    seed_definition(tmp_path, DEFINITION)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert harness.projection.snapshot().repository_names == ()


def test_finisher_factory_receives_step_config_and_a_lazy_inner_thunk(tmp_path):
    """The factory shape (step, config, inner) is what lets a finisher *wrap*
    the step's own agent behavior instead of only replacing it (ADR-0018).
    `inner` is a zero-arg thunk, not an already-built behavior — resolving it
    eagerly for every bound step would call `catalog.get(step)` even for a
    step (like `land`) that is never expected to have a catalog agent."""
    definition = {
        **PUBLISH_DEFINITION,
        "finishers": {"publish": {"kind": "record", "note": "hello"}},
    }
    seed_definition(tmp_path, definition)
    seen = {}

    def factory(step, config, inner):
        seen["step"] = step
        seen["config"] = config
        seen["inner"] = inner()
        return RecordingFinisher()

    build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        finishers={"record": factory},
    )

    assert seen["step"] == "publish"
    assert seen["config"] == {"note": "hello"}
    assert isinstance(seen["inner"], ConsumerBehavior)


def test_finisher_that_never_calls_inner_never_triggers_catalog_lookup(tmp_path):
    """A step exclusively finished by a factory that ignores `inner` (like
    "open-pr") must not force a catalog lookup for that step — the landing
    step normally has no catalog agent at all (`_write_default_agents` skips
    it), so eagerly resolving inner for every bound step would break the most
    common deployment shape."""
    definition = {
        "name": "default",
        "start": "plan",
        "transitions": [
            {"from": "plan", "on": "done", "to": "land"},
            {"from": "land", "on": "done", "to": "end"},
        ],
    }
    seed_definition(tmp_path, definition)
    catalog = MemoryAgentCatalog(
        {"plan": AgentSpec(name="plan", prompt="plan it", allowed_outcomes=(DONE,))}
    )

    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        catalog=catalog,
        runner=object(),
    )

    by_actor = {consumer.actor: consumer for consumer in harness.consumers}
    assert isinstance(by_actor["consumer:land"]._behavior, LandingBehavior)


def test_conflicting_finisher_bindings_with_same_kind_but_different_config_fails(
    tmp_path,
):
    """Two served workflows binding the same step to the same kind but
    different config is still a build-time conflict — the comparison covers
    the whole binding, not just the kind string."""
    layout = seed_definition(
        tmp_path, {**PUBLISH_DEFINITION, "finishers": {"publish": {"kind": "record", "labels": {"done": "a"}}}}
    )
    other = {
        **PUBLISH_DEFINITION,
        "name": "other",
        "finishers": {"publish": {"kind": "record", "labels": {"done": "b"}}},
    }
    (layout.workflows / "other.json").write_text(json.dumps(other))

    with pytest.raises(ValueError, match="conflicting"):
        build(
            tmp_path,
            ["default", "other"],
            events=MemoryEventSink(),
            finishers={"record": lambda step, config, inner: RecordingFinisher()},
        )


# --- issue_import_factory (invariant #43) ------------------------------------


def test_build_without_issue_import_factory_yields_null_issue_import(tmp_path):
    from harness.ports.issue_import import NullIssueImport

    seed_definition(tmp_path, DEFINITION)

    harness = build(tmp_path, "default", events=MemoryEventSink())

    assert isinstance(harness.issue_import, NullIssueImport)
    result = harness.issue_import.add("o/r#1")
    assert result.ok is False
    assert "not configured" in result.error


def test_build_with_issue_import_factory_receives_the_harness_own_queues(tmp_path):
    """The factory `build()` invokes must be handed the exact queues `build()`
    itself constructed, not fresh/disconnected ones — otherwise a task the
    service puts into "inbox" would never reach the dispatcher."""
    seed_definition(tmp_path, DEFINITION)
    seen = {}

    class RecordingIssueImport:
        def add(self, ref):
            raise NotImplementedError

    def factory(**kwargs):
        seen.update(kwargs)
        return RecordingIssueImport()

    harness = build(
        tmp_path, "default", events=MemoryEventSink(), issue_import_factory=factory
    )

    assert seen["inbox"] is harness._inbox
    assert seen["done"] is harness._done
    assert seen["failed"] is harness._failed
    assert seen["healed"] is harness._healed
    assert seen["archived"] is harness._archived
    assert set(seen["step_queues"]) == set(harness._step_queues)
    assert isinstance(harness.issue_import, RecordingIssueImport)


def test_build_issue_import_events_reach_the_projection(tmp_path):
    """The factory's `events` kwarg must be the composed `CompositeEventSink`
    (built before the factory runs) — otherwise an "ingested" event the
    service emits never reaches the board projection."""
    from harness.drivers.github_client import FakeGithubClient, Issue
    from harness.drivers.github_issue_import import GithubIssueImportService
    from harness.drivers.memory import MemoryRepositoryRegistry

    seed_definition(tmp_path, DEFINITION)
    registry = MemoryRepositoryRegistry({"repo": tmp_path})
    client = FakeGithubClient([Issue(1, "t", "b", "u", ())])

    def factory(*, inbox, step_queues, done, failed, healed, archived, events, clock):
        return GithubIssueImportService(
            client=client,
            registry=registry,
            inbox=inbox,
            step_queues=step_queues,
            done=done,
            failed=failed,
            healed=healed,
            archived=archived,
            events=events,
            clock=clock,
            worktree_root=str(tmp_path / "worktrees"),
            slug_of=lambda path: "o/repo",
        )

    harness = build(
        tmp_path, "default", events=MemoryEventSink(), issue_import_factory=factory
    )

    result = harness.issue_import.add("o/repo#1")

    assert result.ok is True
    assert harness.projection.get(result.task_id) is not None
