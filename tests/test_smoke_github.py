"""Smoke fáze 4 — konektor end-to-end na fake GitHubu.

Plně in-memory až na záměr fáze: seedovaný issue s `harness:todo` proteče
smyčkou přes GithubTaskSource a skončí jako `harness:pr-open` s taskem v `done/`.
Tenhle smoke NESAHÁ na síť ani disk (kromě front) — je to e2e konektoru, ne
reálného GitHubu. Reálné HTTP je záměna clientu (HttpGithubClient).
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
    raise AssertionError("smyčka se nezklidnila")


async def test_issue_flows_to_pr_open_and_done(tmp_path):
    seed(tmp_path)
    client = FakeGithubClient(
        [Issue(1, "Fix bug", "detaily", "https://gh/o/r/issues/1", ("harness:todo",))]
    )
    source = GithubTaskSource(
        client=client,
        clock=FakeClock(),
        repo="o/r",
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

    # issue skončil na pr-open, ostatní managed labely pryč, cizí nedotčené
    assert set(client._issues[1].labels) == {"harness:pr-open"}

    # task doputoval do done/
    done_files = list((tmp_path / "done").glob("*.json"))
    assert len(done_files) == 1
    finished = Task.from_dict(json.loads(done_files[0].read_text()))
    assert finished.status == "end"
    assert finished.data["source"]["issue"] == 1
