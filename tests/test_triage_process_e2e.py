"""End-to-end for the triage Process: a `github-issues` scan of a triage label,
a PM-persona step (here a `ScriptedBehavior` stand-in) gated by the
`label-issue` finisher, relabeling the source issue by verdict.

Mirrors `test_processes_e2e.py`'s style but exercises the two pieces this task
adds together: `claimed_label` threaded through the `github-issues` action
(scan `harness:triage`, claim into `harness:validating`, distinct from the
ingestion process's `harness:todo`/`harness:queued` pair) and the `label-issue`
finisher wrapping the step's own behavior to apply `done -> harness:todo` /
`request_changes -> harness:needs-info` on the same issue.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from harness.app import HarnessLayout, build
from harness.drivers.github_client import FakeGithubClient, Issue
from harness.drivers.label_issue import LabelIssueBehavior
from harness.drivers.memory import (
    FakeClock,
    MemoryArtifactStore,
    MemoryEventSink,
    MemoryForge,
    MemoryRepositoryRegistry,
    MemoryWorkspace,
    ScriptedBehavior,
)
from harness.models import DONE, REQUEST_CHANGES, Task

MAX_STEPS = 1000

TRIAGE_WORKFLOW = {
    "name": "triage",
    "start": "triage",
    "transitions": [
        {"from": "triage", "on": "done", "to": "end"},
        {"from": "triage", "on": "request_changes", "to": "end"},
    ],
    "finishers": {
        "triage": {
            "kind": "label-issue",
            "labels": {"done": "harness:todo", "request_changes": "harness:needs-info"},
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


def seed(tmp_path: Path) -> None:
    layout = HarnessLayout(tmp_path)
    layout.workflows.mkdir(parents=True, exist_ok=True)
    (layout.workflows / "triage.json").write_text(json.dumps(TRIAGE_WORKFLOW))


async def test_triage_process_claims_labels_and_hands_off_to_ingestion(tmp_path):
    from harness.cli import _process_check_factories
    import harness.drivers.github_issues_check as ghi_mod

    seed(tmp_path)
    (tmp_path / "processes").mkdir()
    (tmp_path / "processes" / "triage.json").write_text(
        json.dumps(
            {
                "trigger": {"interval": "5m"},
                "action": {
                    "check": "github-issues",
                    "params": {
                        "label": "harness:triage",
                        "claimed_label": "harness:validating",
                    },
                },
                "target": {"workflow": "triage"},
                "dedup": "per-state",
                "sink": {"kind": "none"},
            }
        )
    )

    client = FakeGithubClient(
        [
            Issue(1, "well-defined issue", "body", "https://gh/i/1", ("harness:triage",)),
            Issue(2, "vague issue", "body", "https://gh/i/2", ("harness:triage",)),
        ]
    )
    registry = MemoryRepositoryRegistry({"heblo": Path("/repos/heblo")})
    slug = "onpaj/Anela.Heblo"
    orig_slug = ghi_mod.github_slug
    ghi_mod.github_slug = lambda path: slug  # type: ignore[assignment]

    def finisher_factory(step, config, inner):
        return LabelIssueBehavior(inner=inner(), client=client, labels=config.get("labels", {}))

    try:
        clock = FakeClock("2026-07-23T10:00:00Z")
        args = argparse.Namespace(worktree_root=None, github_label="harness:todo")
        # Process compilation happens inside `build()` now (ADR-0018); the
        # github-issues check factory it can't build itself is supplied via
        # `extra_checks`, exactly as `cli._process_check_factories` does in
        # production. `build()` auto-discovers and compiles `tmp_path/processes`.
        extra_checks = _process_check_factories(args, registry, client=client)

        # ScriptedBehavior stands in for the PM persona (a real deployment
        # would use a catalog agent instead): the first task through "triage"
        # is approved, the second isn't.
        behavior = ScriptedBehavior({"triage": [DONE, REQUEST_CHANGES]})

        harness = build(
            tmp_path,
            "triage",
            events=MemoryEventSink(),
            clock=clock,
            behavior=behavior,
            workspace=MemoryWorkspace(),
            artifacts=MemoryArtifactStore(),
            forge=MemoryForge(),
            delay=0.0,
            extra_checks=extra_checks,
            finishers={"label-issue": finisher_factory},
        )
        source = harness.pollers[0]._source

        await drive_until_quiet(harness)

        # Both issues were claimed at scan time: harness:triage -> harness:validating.
        assert client.list_issues(slug, label="harness:triage") == []

        # One issue got harness:todo (the scripted "done" verdict), the other
        # harness:needs-info (the scripted "request_changes" verdict) — which
        # issue number lands in which bucket depends on dispatch order, not
        # asserted here.
        approved = client.list_issues(slug, label="harness:todo")
        rejected = client.list_issues(slug, label="harness:needs-info")
        assert len(approved) == 1
        assert len(rejected) == 1
        assert {approved[0].number, rejected[0].number} == {1, 2}

        done_files = list((tmp_path / "done").glob("*.json"))
        assert len(done_files) == 2
        statuses = {Task.from_dict(json.loads(p.read_text())).status for p in done_files}
        assert statuses == {"end"}

        # Re-scan after claiming: neither issue carries harness:triage anymore,
        # so a later bucket yields nothing new — no re-claim.
        clock.instant = "2026-07-23T10:10:00Z"
        assert source.poll() == []
    finally:
        ghi_mod.github_slug = orig_slug  # type: ignore[assignment]
