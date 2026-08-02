"""`MergePrBehavior` — the `merge-pr` finisher.

The load-bearing property under test is the asymmetry: many paths refuse to
merge, none merges by accident. Every "bad input" test below asserts that
`merger.merged` stayed empty, not merely that the summary reads nicely.
"""

from __future__ import annotations

import pytest

from harness.behaviors.merge_pr import MergePrBehavior
from harness.drivers.memory import MemoryArtifactStore, MemoryPullRequestMerger
from harness.models import DONE, BehaviorResult, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.pr_merge import MergeError

VERDICT = '```json\n{"confidence": 0.95, "reasoning": "small and covered"}\n```'


def _task(**overrides):
    data = {
        "source": {
            "kind": "pull-request",
            "repo": "onpaj/harness_v2",
            "pr": 85,
            "url": "https://gh/pr/85",
            "base": "main",
            "head_sha": "3035f7dcafe",
            "pr_title": "Fix retries",
        }
    }
    data.update(overrides.pop("data", {}))
    return Task(
        id="tsk_1",
        created="2026-07-27T10:00:00Z",
        status=overrides.pop("status", "merge"),
        repository="harness_v2",
        data=data,
        **overrides,
    )


def _behavior(artifacts, merger, *, artifact=VERDICT, step="merge-review", **kwargs):
    if artifact is not None:
        artifacts.begin("tsk_1", step).put("review.md", artifact)
    kwargs.setdefault("dry_run", False)
    return MergePrBehavior(
        merger=merger, artifacts=artifacts, from_step=step, **kwargs
    )


@pytest.mark.asyncio
async def test_merges_when_confidence_clears_the_threshold():
    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    behavior = _behavior(artifacts, merger, min_confidence=0.8, method="squash")

    result = await behavior.run(_task())

    assert result.outcome == DONE
    assert len(merger.merged) == 1
    (merge,) = merger.merged
    assert merge["repo"] == "onpaj/harness_v2"
    assert merge["number"] == 85
    assert merge["method"] == "squash"
    # The safety pin travels all the way to the driver.
    assert merge["expected_sha"] == "3035f7dcafe"
    assert result.data["merge"]["sha"] == merge["ref"].sha
    assert result.data["merge"]["confidence"] == 0.95


@pytest.mark.asyncio
async def test_the_merge_commit_body_records_why_it_merged():
    """A merge nobody can trace back to a decision is worse than no automation."""
    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    behavior = _behavior(
        artifacts,
        merger,
        artifact=(
            '```json\n{"confidence": 0.9, "reasoning": "covered by tests", '
            '"risks": ["touches the retry path"]}\n```'
        ),
    )

    await behavior.run(_task())

    body = merger.merged[0]["body"]
    assert "task tsk_1" in body
    assert "0.90" in body
    assert "covered by tests" in body
    assert "touches the retry path" in body
    assert merger.merged[0]["title"] == "Fix retries"


@pytest.mark.asyncio
async def test_confidence_below_the_threshold_refuses_without_merging():
    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    behavior = _behavior(
        artifacts,
        merger,
        artifact='```json\n{"confidence": 0.4, "reasoning": "hard to follow"}\n```',
        min_confidence=0.8,
    )

    result = await behavior.run(_task())

    assert merger.merged == []
    assert result.outcome == DONE  # settles, never fails
    assert "0.40" in result.summary and "0.80" in result.summary
    assert "hard to follow" in result.summary


@pytest.mark.asyncio
async def test_the_threshold_is_the_operators_not_the_agents():
    """The same artifact merges under a lax bar and does not under a strict
    one — the persona cannot lower the gate it is judged against."""
    for threshold, expected in ((0.9, 1), (0.99, 0)):
        artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
        behavior = _behavior(artifacts, merger, min_confidence=threshold)
        await behavior.run(_task())
        assert len(merger.merged) == expected


@pytest.mark.asyncio
async def test_dry_run_records_the_decision_without_merging():
    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    behavior = _behavior(artifacts, merger, dry_run=True)

    result = await behavior.run(_task())

    assert merger.merged == []
    assert result.outcome == DONE
    assert "dry run" in result.summary
    assert result.data["merge"] == {"dryRun": True, "confidence": 0.95}


@pytest.mark.asyncio
async def test_dry_run_is_the_default():
    """Configuring the Process is not the same as trusting it."""
    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    artifacts.begin("tsk_1", "merge-review").put("review.md", VERDICT)
    behavior = MergePrBehavior(
        merger=merger, artifacts=artifacts, from_step="merge-review"
    )

    await behavior.run(_task())

    assert merger.merged == []


@pytest.mark.asyncio
async def test_a_moved_head_is_refused_not_failed():
    """The head moved between the scan and the merge: an ordinary state of the
    world. The next scan sees a new head and reviews it from scratch."""
    artifacts = MemoryArtifactStore()
    merger = MemoryPullRequestMerger(heads={("onpaj/harness_v2", 85): "newsha"})
    behavior = _behavior(artifacts, merger)

    result = await behavior.run(_task())

    assert merger.merged == []
    assert result.outcome == DONE
    assert "refused by the forge" in result.summary


@pytest.mark.asyncio
async def test_a_source_without_a_head_sha_refuses():
    """Without a pin there is no proof the reviewed code is still current."""
    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    behavior = _behavior(artifacts, merger)
    task = _task()
    del task.data["source"]["head_sha"]

    result = await behavior.run(task)

    assert merger.merged == []
    assert "head sha" in result.summary


@pytest.mark.asyncio
async def test_a_task_with_no_pull_request_source_refuses():
    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    behavior = _behavior(artifacts, merger)
    task = _task(data={"source": {"kind": "github", "repo": "o/r", "issue": 3}})

    result = await behavior.run(task)

    assert merger.merged == []
    assert "no pull-request source" in result.summary


@pytest.mark.asyncio
async def test_an_unreadable_verdict_fails_the_task_without_merging():
    """The persona approved but justified nothing readable — a real fault, and
    one worth surfacing rather than silently never merging."""
    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    behavior = _behavior(artifacts, merger, artifact="I think it's fine.")

    with pytest.raises(MergeError, match="no fenced json block"):
        await behavior.run(_task())

    assert merger.merged == []


@pytest.mark.asyncio
async def test_a_missing_artifact_fails_the_task_without_merging():
    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    behavior = _behavior(artifacts, merger, artifact=None)

    with pytest.raises(MergeError, match="no artifact"):
        await behavior.run(_task())

    assert merger.merged == []


@pytest.mark.asyncio
async def test_the_highest_attempt_artifact_wins():
    """Attempt-indexed artifacts (invariant #10): a re-run's verdict is the one
    that counts, not the first attempt's."""
    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    artifacts.begin("tsk_1", "merge-review").put(
        "review.md", '```json\n{"confidence": 0.1}\n```'
    )
    artifacts.begin("tsk_1", "merge-review").put(
        "review.md", '```json\n{"confidence": 0.99}\n```'
    )
    behavior = MergePrBehavior(
        merger=merger,
        artifacts=artifacts,
        from_step="merge-review",
        dry_run=False,
    )

    await behavior.run(_task())

    assert len(merger.merged) == 1


@pytest.mark.asyncio
async def test_the_wrap_shape_runs_the_inner_behavior_and_keeps_its_outcome():
    """`from_step` omitted → wrap: the step's own persona runs first, and its
    outcome is what the dispatcher routes on."""

    class Inner(ConsumerBehavior):
        def __init__(self):
            self.ran = False

        async def run(self, task):
            self.ran = True
            return BehaviorResult("approve", "reviewed")

    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    artifacts.begin("tsk_1", "merge").put("review.md", VERDICT)
    inner = Inner()
    behavior = MergePrBehavior(
        merger=merger, artifacts=artifacts, dry_run=False, inner=inner
    )

    result = await behavior.run(_task())

    assert inner.ran
    assert result.outcome == "approve"
    assert len(merger.merged) == 1


@pytest.mark.asyncio
async def test_merging_an_already_merged_pr_is_not_a_failure():
    """A crash between the API call and the task's delivery must not report a
    failure for work that already succeeded (the port's idempotency clause)."""
    artifacts, merger = MemoryArtifactStore(), MemoryPullRequestMerger()
    behavior = _behavior(artifacts, merger)

    first = await behavior.run(_task())
    second = await behavior.run(_task())

    assert len(merger.merged) == 1
    assert first.data["merge"]["sha"] == second.data["merge"]["sha"]
