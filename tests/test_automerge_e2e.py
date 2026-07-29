"""End-to-end for the automerge Process: a `github-mergeable` scan of clean
harness PRs, a reviewer step (a `ScriptedBehavior` stand-in for the persona)
whose verdict artifact gates the `merge-pr` finisher.

Mirrors `test_triage_process_e2e.py`'s style, but proves the property the unit
tests can only prove piecewise: that a real PR travels scan → review → merge
through the actual dispatcher/consumer loop, and — the part that matters — that
a rejected or low-confidence PR travels the same distance and is *not* merged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.behaviors.merge_pr import MergePrBehavior
from harness.drivers.github_client import FakeGithubClient, PullRequestInfo
from harness.drivers.memory import (
    FakeClock,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryPullRequestMerger,
    MemoryRepositoryRegistry,
    MemoryWorkspace,
    ScriptedBehavior,
)
from harness.models import BehaviorResult, Task
from harness.ports.behavior import ConsumerBehavior

MAX_STEPS = 1000
SLUG = "onpaj/harness_v2"

AUTOMERGE_WORKFLOW = {
    "name": "automerge",
    "start": "merge-review",
    "transitions": [
        {"from": "merge-review", "on": "approve", "to": "merge"},
        {"from": "merge-review", "on": "reject", "to": "end"},
        {"from": "merge", "on": "done", "to": "end"},
    ],
    "finishers": {
        "merge": {
            "kind": "merge-pr",
            "from_step": "merge-review",
            "min_confidence": 0.8,
            "method": "squash",
            "dry_run": False,
        }
    },
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


class ReviewingBehavior(ConsumerBehavior):
    """Stands in for the `merge-review` persona: writes a verdict artifact the
    way the real one would, and returns the scripted outcome."""

    def __init__(self, artifacts, script: dict[int, tuple[str, float]]) -> None:
        self._artifacts = artifacts
        self._script = script

    async def run(self, task: Task) -> BehaviorResult:
        number = task.data["source"]["pr"]
        outcome, confidence = self._script[number]
        self._artifacts.begin(task.id, "merge-review").put(
            "review.md",
            f"Reviewed PR #{number}.\n\n"
            f'```json\n{{"confidence": {confidence}, "reasoning": "scripted"}}\n```',
        )
        return BehaviorResult(outcome, f"reviewed #{number}")


def _pr(number, sha, *, state="clean", labels=(), draft=False):
    return PullRequestInfo(
        number=number,
        url=f"https://gh/pr/{number}",
        head_branch=f"harness/tsk_{number}",
        head_sha=sha,
        base_branch="main",
        mergeable_state=state,
        title=f"PR {number}",
        labels=tuple(labels),
        draft=draft,
    )


async def _run(tmp_path, prs, script):
    from harness.cli import _process_check_factories
    import harness.drivers.github_mergeable_check as gm_mod
    from harness.app import HarnessLayout, build

    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "automerge.json").write_text(json.dumps(AUTOMERGE_WORKFLOW))
    (tmp_path / "processes").mkdir()
    (tmp_path / "processes" / "automerge.json").write_text(
        json.dumps(
            {
                "trigger": {"interval": "5m"},
                "action": {"check": "github-mergeable", "params": {}},
                "target": {"workflow": "automerge"},
                "dedup": "per-state",
                "sink": {"kind": "none"},
            }
        )
    )

    client = FakeGithubClient([])
    for pr in prs:
        client.add_pull_request(pr)
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})
    original_slug = gm_mod.github_slug
    gm_mod.github_slug = lambda path: SLUG  # type: ignore[assignment]

    artifacts = MemoryArtifactStore()
    merger = MemoryPullRequestMerger()

    def merge_pr_factory(step, config, inner):
        return MergePrBehavior(
            merger=merger,
            artifacts=artifacts,
            from_step=config["from_step"],
            min_confidence=config["min_confidence"],
            method=config["method"],
            dry_run=config["dry_run"],
        )

    try:
        args = argparse.Namespace(worktree_root=None, github_label="harness:todo")
        harness = build(
            tmp_path,
            "automerge",
            events=MemoryEventSink(),
            clock=FakeClock("2026-07-27T10:00:00Z"),
            behavior=ReviewingBehavior(artifacts, script),
            workspace=MemoryWorkspace(),
            artifacts=artifacts,
            forge=MemoryForge(),
            delay=0.0,
            extra_checks=_process_check_factories(args, registry, client=client),
            finishers={"merge-pr": merge_pr_factory},
        )
        await drive_until_quiet(harness)
    finally:
        gm_mod.github_slug = original_slug  # type: ignore[assignment]
    return merger, harness


async def test_a_confidently_approved_pr_is_merged(tmp_path):
    merger, _ = await _run(
        tmp_path, [_pr(85, "abc123")], {85: ("approve", 0.95)}
    )

    assert len(merger.merged) == 1
    (merge,) = merger.merged
    assert merge["repo"] == SLUG
    assert merge["number"] == 85
    assert merge["method"] == "squash"
    # The sha the reviewer saw is the sha the merge was pinned to.
    assert merge["expected_sha"] == "abc123"


async def test_a_rejected_pr_never_reaches_the_merge_step(tmp_path):
    merger, _ = await _run(
        tmp_path, [_pr(86, "def456")], {86: ("reject", 0.1)}
    )

    assert merger.merged == []


async def test_an_approved_but_low_confidence_pr_is_not_merged(tmp_path):
    """The review said approve; the operator's threshold said no. The
    threshold wins — that is the whole point of splitting the two."""
    merger, _ = await _run(
        tmp_path, [_pr(87, "aaa111")], {87: ("approve", 0.5)}
    )

    assert merger.merged == []


async def test_several_prs_are_judged_independently(tmp_path):
    merger, _ = await _run(
        tmp_path,
        [_pr(1, "s1"), _pr(2, "s2"), _pr(3, "s3")],
        {1: ("approve", 0.99), 2: ("reject", 0.0), 3: ("approve", 0.2)},
    )

    assert [m["number"] for m in merger.merged] == [1]


async def test_a_vetoed_or_draft_pr_never_becomes_a_task(tmp_path):
    """The exclusions hold through the real loop, not just in the check's own
    unit test: no task, no review, no merge."""
    merger, _ = await _run(
        tmp_path,
        [
            _pr(10, "s10", labels=("harness:no-automerge",)),
            _pr(11, "s11", draft=True),
            _pr(12, "s12", state="dirty"),
        ],
        {},
    )

    assert merger.merged == []


async def test_every_candidate_pr_gets_its_own_task(tmp_path):
    """The `per-state` dedup in the Process file is load-bearing, not
    decorative: `github-mergeable` emits one observation per candidate PR, and
    under the default `per-interval` they would all collapse onto one dedup key
    — `SourcePoller._seen` keeping the first and silently discarding the rest.

    Measured: `per-state` merges all three, `per-interval` merges one. This
    test fails if the Process the docs hand the operator ever loses its
    `"dedup": "per-state"`."""
    merger, _ = await _run(
        tmp_path,
        [_pr(1, "s1"), _pr(2, "s2"), _pr(3, "s3")],
        {1: ("approve", 0.99), 2: ("approve", 0.99), 3: ("approve", 0.99)},
    )

    assert sorted(m["number"] for m in merger.merged) == [1, 2, 3]
