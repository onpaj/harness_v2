"""End-to-end for processes on in-memory drivers.

A Process is a compile-time aggregate: `FilesystemProcessRepository` reads
`processes/*.json` and compiles each into a `ScheduledTrigger` — a `TaskSource`
that rides the existing `sources` list. This mirrors `test_generic_triggers_e2e`
but starts from the *authoring* surface (files on disk), proving the whole
compile → poll → dispatch → done path with no disk beyond the queues and no real
waiting.
"""

import json
from pathlib import Path

from harness.app import HarnessLayout, build
from harness.drivers.checks import BUILTIN_CHECKS
from harness.drivers.fs_processes import FilesystemProcessRepository
from harness.drivers.github_client import FakeGithubClient, PullRequestInfo
from harness.drivers.github_conflicts_check import GithubConflictsCheck
from harness.drivers.memory import (
    FakeAgentRunner,
    FakeClock,
    MemoryAgentCatalog,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryRepositoryRegistry,
    MemoryWorkspace,
    ScriptedBehavior,
)
from harness.models import Outcome, Task
from harness.ports.agent import AgentRun, AgentSpec

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "development"},
    ],
}

MAX_STEPS = 1000


def seed(tmp_path) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFINITION))


def write_process(tmp_path: Path, name: str, body: dict) -> None:
    processes = tmp_path / "processes"
    processes.mkdir(parents=True, exist_ok=True)
    (processes / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")


async def drive_until_quiet(harness) -> int:
    for step in range(MAX_STEPS):
        acted = False
        for poller in harness.pollers:
            if poller.tick():
                acted = True
        if harness.dispatcher.tick():
            acted = True
        for consumer in harness.consumers:
            if await consumer.tick():
                acted = True
        if not acted:
            return step
    raise AssertionError("loop did not settle")


def build_harness(tmp_path, *, clock, behavior=None):
    seed(tmp_path)
    sources = FilesystemProcessRepository(tmp_path / "processes").build(clock=clock)
    return build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=clock,
        behavior=behavior,
        workspace=MemoryWorkspace(),
        artifacts=MemoryArtifactStore(),
        forge=MemoryForge(),
        delay=0.0,
        sources=sources or None,
    )


async def test_process_fires_a_task_that_reaches_done_and_reflects_nothing(tmp_path):
    write_process(
        tmp_path,
        "nightly",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "default"},
            "sink": {"kind": "none"},
        },
    )
    clock = FakeClock()
    harness = build_harness(tmp_path, clock=clock)

    await drive_until_quiet(harness)

    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    finished = Task.from_dict(json.loads(done_files[0].read_text()))
    assert finished.status == "end"
    # v1: a Process compiles to a Trigger that stamps no data.source, so the
    # reflector ignores it — nothing is projected outward (sink is none).
    assert "source" not in finished.data


async def test_process_fires_again_after_the_clock_crosses_a_boundary(tmp_path):
    write_process(
        tmp_path,
        "nightly",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"workflow": "default"},
        },
    )
    clock = FakeClock()
    harness = build_harness(tmp_path, clock=clock)

    await drive_until_quiet(harness)
    assert len(list((tmp_path / "done").glob("*.json"))) == 1

    # Same bucket → no new task.
    await drive_until_quiet(harness)
    assert len(list((tmp_path / "done").glob("*.json"))) == 1

    # Cross the interval boundary → a second, distinct task.
    clock.instant = "2026-07-19T11:00:00Z"
    await drive_until_quiet(harness)
    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 2
    ids = {Task.from_dict(json.loads(p.read_text())).id for p in done_files}
    assert len(ids) == 2


async def test_process_step_target_is_placed_by_the_dispatcher(tmp_path):
    """A workflow-less `step` target: the process emits, the dispatcher places.

    Nobody writes into the step queue directly — the compiled trigger puts the
    task in the inbox and the dispatcher routes it into `development`.
    """
    write_process(
        tmp_path,
        "cleanup",
        {
            "trigger": {"interval": "1h"},
            "action": {"check": "always"},
            "target": {"step": "development"},
        },
    )
    clock = FakeClock()
    behavior = ScriptedBehavior()
    harness = build_harness(tmp_path, clock=clock, behavior=behavior)

    await drive_until_quiet(harness)

    assert "development" in behavior.seen
    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    assert Task.from_dict(json.loads(done_files[0].read_text())).status == "end"


async def test_no_processes_behaves_as_before(tmp_path):
    """Backward-compat: with an empty/absent processes/ the harness runs exactly
    as today — no pollers — and a submitted task still flows to done."""
    clock = FakeClock()
    harness = build_harness(tmp_path, clock=clock)
    assert harness.pollers == []

    task = Task(
        id="tsk_plain",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        data={"title": "manual task"},
    )
    (tmp_path / "tasks" / "tsk_plain.json").write_text(json.dumps(task.to_dict()))

    await drive_until_quiet(harness)

    finished = Task.from_dict(
        json.loads((tmp_path / "done" / "tsk_plain.json").read_text())
    )
    assert finished.status == "end"


def test_github_issues_process_ingests_a_labelled_issue_once_per_bucket(tmp_path):
    from harness.cli import _process_sources
    from harness.drivers.github_client import FakeGithubClient, Issue
    from harness.drivers.memory import MemoryRepositoryRegistry

    (tmp_path / "processes").mkdir()
    (tmp_path / "processes" / "harness-todo.json").write_text(
        '{"trigger": {"interval": "30s"},'
        ' "action": {"check": "github-issues", "params": {"label": "harness:todo"}},'
        ' "target": {"workflow": "default"}, "dedup": "per-state",'
        ' "sink": {"kind": "none"}}'
    )
    client = FakeGithubClient(
        [Issue(42, "Do the thing", "body", "https://gh/i/42", ("harness:todo",))]
    )
    registry = MemoryRepositoryRegistry({"heblo": Path("/repos/heblo")})
    slugs = {Path("/repos/heblo"): "onpaj/Anela.Heblo"}
    clock = FakeClock("2026-07-22T10:00:00Z")

    import argparse

    args = argparse.Namespace(worktree_root=None, github_label="harness:todo")
    # Inject the slug resolver via a subclassed check factory path: pass a client
    # and rely on the registry; slug resolution uses git origin in production, so
    # monkeypatch github_slug for the test.
    import harness.drivers.github_issues_check as mod

    orig = mod.github_slug
    mod.github_slug = slugs.get  # type: ignore[assignment]
    try:
        (source,) = _process_sources(
            args, tmp_path, registry,
            clock=clock, known_targets={"default"}, client=client,
        )

        first = source.poll()
        assert len(first) == 1
        task = first[0]
        assert task.workflow_template == "default"
        assert task.repository == "heblo"
        assert task.data["source"] == {
            "kind": "github", "repo": "onpaj/Anela.Heblo",
            "issue": 42, "url": "https://gh/i/42",
        }

        # Same 30s bucket → no re-fire.
        clock.instant = "2026-07-22T10:00:20Z"
        assert source.poll() == []

        # The issue was claimed (label swapped) → next bucket sees nothing new.
        clock.instant = "2026-07-22T10:01:00Z"
        assert source.poll() == []
    finally:
        mod.github_slug = orig  # type: ignore[assignment]


async def test_github_issues_process_reflects_task_state_onto_issue_labels(tmp_path):
    """`--no-github-source` configuration: ingestion via the `github-issues`
    Process action, reflection via a standalone `GithubLabelReflector` —
    restores the outward half of the GitHub round-trip for a Process-sourced
    task (the regression this task fixes)."""
    import argparse

    import harness.drivers.github_issues_check as mod
    from harness.cli import DEFAULT_STEP_LABELS, _process_sources
    from harness.drivers.github_client import FakeGithubClient, Issue
    from harness.drivers.github_source import GithubLabelReflector

    seed(tmp_path)
    write_process(
        tmp_path,
        "harness-todo",
        {
            "trigger": {"interval": "30s"},
            "action": {"check": "github-issues", "params": {"label": "harness:todo"}},
            "target": {"workflow": "default"},
            "dedup": "per-state",
            "sink": {"kind": "none"},
        },
    )
    client = FakeGithubClient(
        [Issue(42, "Do the thing", "body", "https://gh/i/42", ("harness:todo",))]
    )
    registry = MemoryRepositoryRegistry({"heblo": Path("/repos/heblo")})
    slugs = {Path("/repos/heblo"): "onpaj/Anela.Heblo"}
    clock = FakeClock("2026-07-22T10:00:00Z")

    args = argparse.Namespace(worktree_root=None, github_label="harness:todo")
    orig = mod.github_slug
    mod.github_slug = slugs.get  # type: ignore[assignment]
    try:
        process_sources = _process_sources(
            args, tmp_path, registry,
            clock=clock, known_targets={"default"}, client=client,
        )
        # The outward half — registered standalone, exactly as `cli._run` does
        # whenever `--no-github-source` delegates ingestion to the process.
        reflector = GithubLabelReflector(
            client=client, repo="onpaj/Anela.Heblo", step_labels=DEFAULT_STEP_LABELS
        )
        behavior = ScriptedBehavior()
        harness = build(
            tmp_path,
            "default",
            events=MemoryEventSink(),
            clock=clock,
            behavior=behavior,
            workspace=MemoryWorkspace(),
            artifacts=MemoryArtifactStore(),
            forge=MemoryForge(),
            delay=0.0,
            sources=[*process_sources, reflector],
        )

        await drive_until_quiet(harness)

        # `land` is always the built-in LandingBehavior (opens the PR), never
        # the scripted one — only the earlier steps run through `behavior`.
        assert "development" in behavior.seen
        assert "review" in behavior.seen
        # Terminal label reflects success; the earlier queued/step labels were
        # each set and then superseded (a stateless recompute each time).
        assert set(client._issues[42].labels) == {"harness:pr-open"}

        done_files = list((tmp_path / "done").glob("*.json"))
        assert len(done_files) == 1
        assert Task.from_dict(json.loads(done_files[0].read_text())).status == "end"
    finally:
        mod.github_slug = orig  # type: ignore[assignment]


# --- `github-conflicts` process: parity with the retired GithubMergeabilityWatcher ---
#
# These three replace test_mergeability_e2e.py's scenarios, proving the same
# outcomes purely through a `processes/autoresolver.json`-shaped process
# compiled by `FilesystemProcessRepository`, not a hand-built watcher.

# A minimal "default" workflow with only a "plan" step — the catalog these
# tests wire only knows "plan" and "resolve", unlike this file's own
# module-level DEFINITION (plan/development/review/land).
AUTORESOLVER_DEFAULT_DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [{"from": "plan", "on": "done", "to": "end"}],
}

RESOLVER_DEFINITION = {
    "name": "resolver",
    "start": "resolve",
    "transitions": [
        {"from": "resolve", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
    ],
}

AUTORESOLVER_PROCESS = {
    "trigger": {"interval": "60s"},
    "action": {"check": "github-conflicts", "params": {"head_prefix": "harness/"}},
    "target": {"workflow": "resolver"},
    "dedup": "per-state",
    "sink": {"kind": "none"},
}


def _seed_autoresolver_workflows(tmp_path: Path) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(AUTORESOLVER_DEFAULT_DEFINITION))
    (layout.workflows / "resolver.json").write_text(json.dumps(RESOLVER_DEFINITION))


def _build_autoresolver_sources(tmp_path: Path, client, registry, slug_of, clock):
    """Mirrors `cli._process_sources`'s `github_conflicts_factory`, but with an
    injected `slug_of` instead of a `github_slug` monkeypatch — the client and
    registry are already in hand, so there is no need to touch git remotes."""

    def github_conflicts_factory(params: dict) -> GithubConflictsCheck:
        return GithubConflictsCheck(
            client=client,
            registry=registry,
            slug_of=slug_of,
            head_prefix=params.get("head_prefix", "harness/"),
        )

    return FilesystemProcessRepository(tmp_path / "processes").build(
        clock=clock,
        checks={**BUILTIN_CHECKS, "github-conflicts": github_conflicts_factory},
        known_targets={"default", "resolver"},
    )


def build_autoresolver_harness(tmp_path, client, workspace):
    _seed_autoresolver_workflows(tmp_path)
    write_process(tmp_path, "autoresolver", AUTORESOLVER_PROCESS)

    registry = MemoryRepositoryRegistry({"app": Path("/repos/app")})
    slugs = {Path("/repos/app"): "o/r"}
    clock = FakeClock()
    sources = _build_autoresolver_sources(tmp_path, client, registry, slugs.get, clock)

    catalog = MemoryAgentCatalog(
        {
            "plan": AgentSpec(name="plan", prompt="plan the work"),
            "resolve": AgentSpec(name="resolve", prompt="resolve the conflict"),
        }
    )
    runner = FakeAgentRunner(
        default=AgentRun(Outcome.DONE, "done"),
        runs={"resolve": AgentRun(Outcome.DONE, "resolve: fixed conflict")},
    )
    harness = build(
        tmp_path,
        ["default", "resolver"],
        events=MemoryEventSink(),
        clock=clock,
        workspace=workspace,
        artifacts=MemoryArtifactStore(),
        forge=MemoryForge(),
        catalog=catalog,
        runner=runner,
        sources=sources,
        delay=0.0,
    )
    return harness, (sources[0] if sources else None), registry, slugs


async def test_dirty_pr_via_autoresolver_process_flows_through_resolver_to_a_single_pr_on_the_same_branch(
    tmp_path,
):
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(42, "https://github.com/o/r/pull/42", "harness/tsk_original", "sha1", "main", "dirty")
    )
    workspace = MemoryWorkspace()
    harness, _, _, _ = build_autoresolver_harness(tmp_path, client, workspace)

    await drive_until_quiet(harness)

    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    finished = Task.from_dict(json.loads(done_files[0].read_text()))
    assert finished.status == "end"
    assert finished.data["source"]["kind"] == "mergeability"
    assert finished.data["source"]["pr"] == 42

    # Landing opened exactly one PR — the resolver worked on the PR's own
    # branch, so this must not be a second, duplicate PR.
    handle = workspace.handles[finished.id]
    assert handle.branch == "harness/tsk_original"


async def test_behind_pr_via_autoresolver_process_is_auto_updated_with_no_task_created(tmp_path):
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(7, "https://github.com/o/r/pull/7", "harness/tsk_7", "sha7", "main", "behind")
    )
    workspace = MemoryWorkspace()
    harness, _, _, _ = build_autoresolver_harness(tmp_path, client, workspace)

    await drive_until_quiet(harness)

    assert client.updated_branches == [("o/r", 7)]
    assert list((tmp_path / "done").glob("*.json")) == []
    assert list((tmp_path / "tasks").glob("*.json")) == []


async def test_restart_does_not_duplicate_the_autoresolver_task_for_the_same_conflict(tmp_path):
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(42, "https://github.com/o/r/pull/42", "harness/tsk_original", "sha1", "main", "dirty")
    )
    workspace = MemoryWorkspace()
    harness, _, registry, slugs = build_autoresolver_harness(tmp_path, client, workspace)

    await drive_until_quiet(harness)
    assert len(list((tmp_path / "done").glob("*.json"))) == 1

    # Simulate a restart: a fresh ScheduledTrigger compiled from the same
    # processes/autoresolver.json (not a re-used check instance, so its own
    # in-process `_seen` is empty and irrelevant here on purpose), seeded from
    # every task now on disk — the cross-restart dedup path this proves is
    # `ScheduledTrigger`'s `per-state` dedup_key + `SourcePoller._seen`.
    from harness.source_poller import SourcePoller

    fresh_clock = FakeClock()
    (fresh_source,) = _build_autoresolver_sources(
        tmp_path, client, registry, slugs.get, fresh_clock
    )
    events = MemoryEventSink()
    fresh_poller = SourcePoller(source=fresh_source, inbox=harness._inbox, events=events)
    existing = [
        *harness._inbox.list(),
        *(task for queue in harness._step_queues.values() for task in queue.list()),
        *harness._done.list(),
        *harness._failed.list(),
    ]
    fresh_poller.seed(existing)

    assert fresh_poller.tick() is False
