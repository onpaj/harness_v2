"""End-to-end: mergeability watcher on in-memory drivers.

A dirty PR → resolver task → resolve (merge conflict, agent fixes it) → land
(pushes back onto the *same* PR, idempotently) → end. A behind PR is
auto-updated with no task at all. Mirrors test_phase4_e2e.py's shape — no
disk (except the queues) and no real waiting.
"""

import json

from harness.app import HarnessLayout, build
from harness.drivers.github_client import FakeGithubClient, PullRequestInfo
from harness.drivers.memory import (
    FakeAgentRunner,
    FakeClock,
    MemoryAgentCatalog,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryWorkspace,
)
from harness.drivers.mergeability_watcher import GithubMergeabilityWatcher
from harness.models import Outcome, Task
from harness.ports.agent import AgentRun, AgentSpec

DEFAULT_DEFINITION = {
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

MAX_STEPS = 1000


def seed(tmp_path) -> HarnessLayout:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "default.json").write_text(json.dumps(DEFAULT_DEFINITION))
    (layout.workflows / "resolver.json").write_text(json.dumps(RESOLVER_DEFINITION))
    return layout


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


def build_harness(tmp_path, client, workspace):
    layout = seed(tmp_path)
    watcher = GithubMergeabilityWatcher(
        client=client,
        clock=FakeClock(),
        repo="o/r",
        repository="app",
        worktree_root=str(layout.worktrees),
    )
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
        clock=FakeClock(),
        workspace=workspace,
        artifacts=MemoryArtifactStore(),
        forge=MemoryForge(),
        catalog=catalog,
        runner=runner,
        sources=[watcher],
        delay=0.0,
    )
    return harness, watcher


async def test_dirty_pr_flows_through_resolver_to_a_single_pr_on_the_same_branch(tmp_path):
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(42, "https://github.com/o/r/pull/42", "harness/tsk_original", "sha1", "main", "dirty")
    )
    workspace = MemoryWorkspace()
    harness, _ = build_harness(tmp_path, client, workspace)

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


async def test_behind_pr_is_auto_updated_with_no_task_created(tmp_path):
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(7, "https://github.com/o/r/pull/7", "harness/tsk_7", "sha7", "main", "behind")
    )
    workspace = MemoryWorkspace()
    harness, _ = build_harness(tmp_path, client, workspace)

    await drive_until_quiet(harness)

    assert client.updated_branches == [("o/r", 7)]
    assert list((tmp_path / "done").glob("*.json")) == []
    assert list((tmp_path / "tasks").glob("*.json")) == []


async def test_restart_does_not_duplicate_the_resolver_task_for_the_same_conflict(tmp_path):
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(42, "https://github.com/o/r/pull/42", "harness/tsk_original", "sha1", "main", "dirty")
    )
    workspace = MemoryWorkspace()
    harness, watcher = build_harness(tmp_path, client, workspace)

    await drive_until_quiet(harness)
    assert len(list((tmp_path / "done").glob("*.json"))) == 1

    # Simulate a restart: seed a fresh poller from what's on disk, then poll
    # again for the same still-"dirty" (never re-checked) PR — a real watcher
    # would normally see it turn clean once landed, but here we assert the
    # dedup layer itself: same repo/pr/head_sha must never double-queue.
    from harness.source_poller import SourcePoller

    events = MemoryEventSink()
    fresh_poller = SourcePoller(source=watcher, inbox=harness._inbox, events=events)
    existing = [
        *harness._inbox.list(),
        *(task for queue in harness._step_queues.values() for task in queue.list()),
        *harness._done.list(),
        *harness._failed.list(),
    ]
    fresh_poller.seed(existing)

    assert fresh_poller.tick() is False
