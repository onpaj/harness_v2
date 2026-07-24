"""Phase 4 smoke — the connector end-to-end on a fake GitHub.

Fully in-memory apart from the phase's intent: a seeded issue with `harness:todo`
flows through the loop via GithubTaskSource and ends up as `harness:pr-open` with
the task in `done/`. This smoke DOES NOT touch the network or disk (except the
queues) — it's an e2e of the connector, not of real GitHub. Real HTTP is a swap
of the client (HttpGithubClient).
"""

import json

from harness.app import HarnessLayout, build
from harness.drivers.github_client import FakeGithubClient, Issue
from harness.drivers.github_source import GithubTaskSource
from harness.drivers.memory import (
    FakeClock,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryWorkspace,
)
from harness.models import Task

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


async def drive_until_quiet(harness) -> None:
    for _ in range(MAX_STEPS):
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
            return
    raise AssertionError("loop did not settle")


async def test_issue_flows_to_pr_open_and_done(tmp_path):
    seed(tmp_path)
    client = FakeGithubClient(
        [Issue(1, "Fix bug", "details", "https://gh/o/r/issues/1", ("harness:todo",))]
    )
    source = GithubTaskSource(
        client=client,
        clock=FakeClock(),
        repo="o/r",
        workflow="default",
        repository="/repos/r",
        worktree_root="/wt",
        step_labels={
            "development": "harness:in-progress",
            "review": "harness:in-review",
            "land": "harness:landing",
        },
    )
    harness = build(
        tmp_path,
        "default",
        events=MemoryEventSink(),
        clock=FakeClock(),
        workspace=MemoryWorkspace(),
        artifacts=MemoryArtifactStore(),
        forge=MemoryForge(),
        delay=0.0,
        sources=[source],
    )

    await drive_until_quiet(harness)

    # the issue ended up at pr-open, other managed labels gone, foreign ones untouched
    assert set(client._issues[1].labels) == {"harness:pr-open"}

    # the task made it to done/
    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    finished = Task.from_dict(json.loads(done_files[0].read_text()))
    assert finished.status == "end"
    assert finished.data["source"]["issue"] == 1
