"""unblock-pr, end to end: a real PR travels scan → unblock → land → end, and
a PR that has spent its attempt budget travels no distance at all.

Mirrors `test_automerge_e2e.py`'s style, but proves the property the unit tests
can only prove piecewise: that `GithubUnhealthyPrsCheck`'s brief survives the
dispatcher/consumer loop intact, and that the give-up label actually stops the
process rather than merely being written.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.drivers.github_client import CheckRun, FakeGithubClient, PullRequestInfo
from harness.drivers.memory import (
    FakeClock,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryRepositoryRegistry,
    MemoryWorkspace,
)
from harness.models import DONE, BehaviorResult, Task
from harness.ports.behavior import ConsumerBehavior

MAX_STEPS = 1000
SLUG = "onpaj/harness_v2"

UNBLOCK_WORKFLOW = {
    "name": "unblock-pr",
    "start": "unblock",
    "transitions": [
        {"from": "unblock", "on": "done", "to": "land"},
        {"from": "unblock", "on": "stuck", "to": "end"},
        {"from": "land", "on": "done", "to": "end"},
    ],
}


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


class CapturingBehavior(ConsumerBehavior):
    """Stands in for every step: records the task it saw and passes it on."""

    def __init__(self) -> None:
        self.seen: list[Task] = []

    async def run(self, task: Task) -> BehaviorResult:
        self.seen.append(task)
        return BehaviorResult(DONE, f"{task.status}: ok")


def _pr(number, sha, *, state, labels=(), head_repo=SLUG):
    return PullRequestInfo(
        number=number,
        url=f"https://gh/pr/{number}",
        head_branch=f"feature/{number}",
        head_sha=sha,
        base_branch="main",
        mergeable_state=state,
        title=f"PR {number}",
        labels=tuple(labels),
        head_repo=head_repo,
    )


async def _run(tmp_path, prs, check_runs=(), logs=()):
    from harness.cli import _process_check_factories
    import harness.drivers.github_unhealthy_prs_check as up_mod
    from harness.app import HarnessLayout, build

    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "unblock-pr.json").write_text(json.dumps(UNBLOCK_WORKFLOW))
    (tmp_path / "processes").mkdir()
    (tmp_path / "processes" / "resolve-failing-pr.json").write_text(
        json.dumps(
            {
                "trigger": {"interval": "60s"},
                "action": {
                    "check": "github-unhealthy-prs",
                    "params": {"head_prefix": ""},
                },
                "target": {"workflow": "unblock-pr"},
                "dedup": "per-state",
                "sink": {"kind": "none"},
            }
        )
    )

    client = FakeGithubClient([])
    for pr in prs:
        client.add_pull_request(pr)
    for sha, run in check_runs:
        client.add_check_run(sha, run)
    for run_id, text in logs:
        client.set_check_run_log(run_id, text)

    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})
    original_slug = up_mod.github_slug
    up_mod.github_slug = lambda path: SLUG  # type: ignore[assignment]

    behavior = CapturingBehavior()
    try:
        args = argparse.Namespace(worktree_root=None, github_label="harness:todo")
        harness = build(
            tmp_path,
            "unblock-pr",
            events=MemoryEventSink(),
            clock=FakeClock("2026-07-31T10:00:00Z"),
            behavior=behavior,
            workspace=MemoryWorkspace(),
            artifacts=MemoryArtifactStore(),
            forge=MemoryForge(),
            delay=0.0,
            extra_checks=_process_check_factories(args, registry, client=client),
            # `build()` defaults `landing_step` to "land" and auto-binds it to
            # the real `open-pr` finisher when the served workflow declares no
            # `finishers` of its own (ADR-0016) — which would silently swap
            # out `CapturingBehavior` for the real `LandingBehavior` on this
            # workflow's own "land" step. Point the default at a step name
            # this workflow doesn't have so "land" stays on the stand-in,
            # exactly like "unblock": this e2e is about the observation
            # surviving the dispatcher/consumer loop, not about landing.
            landing_step="__unused_landing_step__",
        )
        await drive_until_quiet(harness)
    finally:
        up_mod.github_slug = original_slug  # type: ignore[assignment]
    return behavior, client


async def test_a_conflicted_pr_travels_unblock_then_land(tmp_path):
    behavior, client = await _run(tmp_path, [_pr(85, "abc123", state="dirty")])

    steps = [task.status for task in behavior.seen]
    assert steps == ["unblock", "land"]
    problem = behavior.seen[0].data["problem"]
    assert problem["conflicted"] is True
    assert problem["attempt"] == 1
    assert client.list_pull_requests(SLUG)[0].labels == ("harness:autofix-1@abc123",)


async def test_a_red_pr_carries_its_log_tail_through_the_loop(tmp_path):
    behavior, _ = await _run(
        tmp_path,
        [_pr(7, "deadbee", state="unstable")],
        check_runs=[("deadbee", CheckRun(1, "pytest", "failure", "https://gh/run/1"))],
        logs=[(1, "E   AssertionError: nope\n")],
    )

    problem = behavior.seen[0].data["problem"]
    assert problem["conflicted"] is False
    assert problem["failing_checks"][0]["name"] == "pytest"
    assert "AssertionError: nope" in problem["failing_checks"][0]["log_tail"]


async def test_a_pr_that_spent_its_budget_is_labelled_and_never_dispatched(tmp_path):
    behavior, client = await _run(
        tmp_path,
        [_pr(9, "s9", state="dirty", labels=("harness:autofix-3@older12",))],
    )

    assert behavior.seen == []
    assert "harness:needs-human" in client.list_pull_requests(SLUG)[0].labels


async def test_a_fork_pr_never_reaches_a_step(tmp_path):
    """The whole loop, not just the check: a fork PR opened from the
    contributor's own `main` must mint no task at all — the workspace would
    otherwise attach to, commit to and push the *base* repo's `main`."""
    fork = _pr(11, "s11", state="dirty", head_repo="contributor/harness_v2")
    behavior, client = await _run(tmp_path, [fork])

    assert behavior.seen == []
    assert client.list_pull_requests(SLUG)[0].labels == ()


async def test_an_opted_out_pr_is_not_touched_at_all(tmp_path):
    behavior, client = await _run(
        tmp_path,
        [_pr(10, "s10", state="dirty", labels=("harness:no-autofix",))],
    )

    assert behavior.seen == []
    assert client.list_pull_requests(SLUG)[0].labels == ("harness:no-autofix",)
