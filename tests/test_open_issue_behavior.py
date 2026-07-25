"""`OpenIssueBehavior` — the generic `open-issue` finisher kind.

Config drives everything: which step's artifact to read (`from_step`, whose
presence also selects replace-vs-wrap), which label to carry and scope the
marker search to, and which per-draft labels are allowed through.
"""

import pytest

from harness.behaviors.open_issue import OpenIssueBehavior
from harness.drivers.memory import MemoryArtifactStore, MemoryIssueTracker
from harness.issue_drafts import marker_for
from harness.models import DONE, REQUEST_CHANGES, BehaviorResult, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.issues import IssueError, IssueTracker


def block(*drafts: str) -> str:
    return "# A report\n\nprose\n\n```json\n[" + ", ".join(drafts) + "]\n```\n"


DRAFT = '{"title": "A finding", "body": "the body", "labels": ["tech-debt"]}'


def task(task_id="tsk_1", *, step="review", repository="Anela.Heblo") -> Task:
    return Task(
        id=task_id,
        created="2026-07-25T06:00:00Z",
        workflow_template="arch-review",
        status=step,
        repository=repository,
    )


def slug_for(name):
    if name is None:
        raise IssueError("the task has no repository")
    return f"onpaj/{name}"


def make(*, tracker, artifacts, label="arch-review", **kwargs) -> OpenIssueBehavior:
    return OpenIssueBehavior(
        tracker=tracker,
        artifacts=artifacts,
        slug_for=slug_for,
        label=label,
        **kwargs,
    )


class StubInner(ConsumerBehavior):
    """Stands in for the step's own agent behavior in the wrap shape."""

    def __init__(self, outcome=DONE, *, artifacts=None, task_id="tsk_1", text=block(DRAFT)):
        self.outcome = outcome
        self.ran = False
        self._artifacts = artifacts
        self._task_id = task_id
        self._text = text

    async def run(self, task):
        self.ran = True
        if self._artifacts is not None:
            self._artifacts.begin(self._task_id, "review").put("review-01.md", self._text)
        return BehaviorResult(self.outcome, "the agent ran")


# --- wrap shape (no from_step) ------------------------------------------


async def test_wrap_runs_the_step_then_files_what_it_wrote():
    artifacts = MemoryArtifactStore()
    tracker = MemoryIssueTracker()
    inner = StubInner(artifacts=artifacts)
    behavior = make(tracker=tracker, artifacts=artifacts, inner=inner)

    result = await behavior.run(task())

    assert inner.ran
    assert result.outcome == DONE
    assert len(tracker.opened) == 1
    opened = tracker.opened[0]
    assert opened["repo"] == "onpaj/Anela.Heblo"
    assert opened["title"] == "A finding"
    assert opened["marker"].startswith("tsk_1:")
    assert opened["scope_label"] == "arch-review"


async def test_wrap_returns_the_inner_outcome_unchanged():
    """Routing is the workflow's business — the finisher never rewrites it."""
    artifacts = MemoryArtifactStore()
    inner = StubInner(REQUEST_CHANGES, artifacts=artifacts)
    behavior = make(tracker=MemoryIssueTracker(), artifacts=artifacts, inner=inner)

    result = await behavior.run(task())

    assert result.outcome == REQUEST_CHANGES


async def test_wrap_files_several_issues_from_one_report():
    artifacts = MemoryArtifactStore()
    tracker = MemoryIssueTracker()
    text = block(DRAFT, '{"title": "Another finding"}', '{"title": "A third"}')
    behavior = make(
        tracker=tracker, artifacts=artifacts, inner=StubInner(artifacts=artifacts, text=text)
    )

    result = await behavior.run(task())

    assert len(tracker.opened) == 3
    assert "3 issues" in result.summary


async def test_an_empty_array_files_nothing_and_still_succeeds():
    artifacts = MemoryArtifactStore()
    tracker = MemoryIssueTracker()
    behavior = make(
        tracker=tracker, artifacts=artifacts, inner=StubInner(artifacts=artifacts, text=block())
    )

    result = await behavior.run(task())

    assert tracker.opened == []
    assert result.outcome == DONE
    assert "no issues to file" in result.summary


async def test_an_inner_failure_propagates_and_files_nothing():
    class Boom(ConsumerBehavior):
        async def run(self, task):
            raise RuntimeError("the agent died")

    tracker = MemoryIssueTracker()
    behavior = make(tracker=tracker, artifacts=MemoryArtifactStore(), inner=Boom())

    with pytest.raises(RuntimeError):
        await behavior.run(task())
    assert tracker.opened == []


# --- replace shape (from_step) -------------------------------------------


async def test_replace_reads_a_named_earlier_step_and_never_runs_an_agent():
    artifacts = MemoryArtifactStore()
    artifacts.begin("tsk_heal", "heal").put("heal-01.md", block('{"title": "Fix the driver"}'))
    tracker = MemoryIssueTracker()
    behavior = make(
        tracker=tracker,
        artifacts=artifacts,
        label="harness:self-heal",
        from_step="heal",
    )

    result = await behavior.run(task("tsk_heal", step="file-issue", repository="harness_v2"))

    assert result.outcome == DONE
    assert tracker.opened[0]["title"] == "Fix the driver"
    assert tracker.opened[0]["scope_label"] == "harness:self-heal"


async def test_replace_with_no_artifact_files_nothing():
    """The healer's `skip` path: the persona wrote no file at all."""
    tracker = MemoryIssueTracker()
    behavior = make(
        tracker=tracker,
        artifacts=MemoryArtifactStore(),
        label="harness:self-heal",
        from_step="heal",
    )

    result = await behavior.run(task("tsk_heal", step="file-issue"))

    assert tracker.opened == []
    assert result.outcome == DONE


async def test_replace_reads_the_latest_attempt():
    artifacts = MemoryArtifactStore()
    artifacts.begin("tsk_heal", "heal").put("heal-01.md", block('{"title": "first pass"}'))
    artifacts.begin("tsk_heal", "heal").put("heal-02.md", block('{"title": "second pass"}'))
    tracker = MemoryIssueTracker()
    behavior = make(tracker=tracker, artifacts=artifacts, from_step="heal")

    await behavior.run(task("tsk_heal", step="file-issue"))

    assert tracker.opened[0]["title"] == "second pass"


# --- labels, markers, errors ---------------------------------------------


async def test_per_draft_labels_are_filtered_against_the_allowlist():
    artifacts = MemoryArtifactStore()
    tracker = MemoryIssueTracker()
    text = block('{"title": "A finding", "labels": ["tech-debt", "invented-label"]}')
    behavior = make(
        tracker=tracker,
        artifacts=artifacts,
        allowed_labels=("tech-debt", "refactoring"),
        inner=StubInner(artifacts=artifacts, text=text),
    )

    result = await behavior.run(task())

    assert tracker.opened[0]["labels"] == ("tech-debt", "arch-review")
    assert "invented-label" in result.summary


async def test_with_no_allowlist_no_per_draft_label_gets_through():
    artifacts = MemoryArtifactStore()
    tracker = MemoryIssueTracker()
    behavior = make(
        tracker=tracker, artifacts=artifacts, inner=StubInner(artifacts=artifacts)
    )

    await behavior.run(task())

    assert tracker.opened[0]["labels"] == ("arch-review",)


async def test_a_rerun_with_the_same_titles_opens_nothing_new():
    tracker = MemoryIssueTracker()
    for _ in range(2):
        artifacts = MemoryArtifactStore()
        behavior = make(
            tracker=tracker, artifacts=artifacts, inner=StubInner(artifacts=artifacts)
        )
        await behavior.run(task())

    assert len(tracker.opened) == 1


async def test_a_rerun_after_a_partial_failure_resumes():
    """Issue 1 was opened before issue 2 blew up; the re-run must not duplicate it."""
    tracker = MemoryIssueTracker()
    artifacts = MemoryArtifactStore()
    text = block('{"title": "first"}', '{"title": "second"}')
    behavior = make(
        tracker=tracker, artifacts=artifacts, inner=StubInner(artifacts=artifacts, text=text)
    )
    tracker.open_issue(
        "onpaj/Anela.Heblo",
        title="first",
        body="",
        labels=(),
        marker=marker_for("tsk_1", "first"),
        scope_label="arch-review",
    )

    await behavior.run(task())

    assert len(tracker.opened) == 2
    assert [o["title"] for o in tracker.opened] == ["first", "second"]


async def test_a_malformed_block_raises_issue_error():
    artifacts = MemoryArtifactStore()
    behavior = make(
        tracker=MemoryIssueTracker(),
        artifacts=artifacts,
        inner=StubInner(artifacts=artifacts, text="# A report with no block\n"),
    )

    with pytest.raises(IssueError, match="fenced json block"):
        await behavior.run(task())


async def test_a_repository_less_task_with_no_drafts_settles_done_and_files_nothing():
    """Invariant #25: a repository-less heal task is the default case for an
    autoheal process with no seeded `params.repository`. With zero drafts,
    `slug_for` must never be called — resolving the slug is what would raise."""
    artifacts = MemoryArtifactStore()
    tracker = MemoryIssueTracker()
    behavior = make(
        tracker=tracker, artifacts=artifacts, inner=StubInner(artifacts=artifacts, text=block())
    )

    result = await behavior.run(task(repository=None))

    assert tracker.opened == []
    assert result.outcome == DONE
    assert "no issues to file" in result.summary


async def test_an_unresolvable_repository_raises_issue_error():
    artifacts = MemoryArtifactStore()
    behavior = make(
        tracker=MemoryIssueTracker(),
        artifacts=artifacts,
        inner=StubInner(artifacts=artifacts),
    )

    with pytest.raises(IssueError, match="no repository"):
        await behavior.run(task(repository=None))


async def test_a_tracker_error_propagates_uncaught():
    """`Consumer.tick` turns this into `failed/`; the recursion guard, not
    in-behavior handling, is what stops a loop."""

    class RaisingTracker(IssueTracker):
        def open_issue(self, repo, *, title, body, labels, marker, scope_label):
            raise IssueError("no token")

    artifacts = MemoryArtifactStore()
    behavior = make(
        tracker=RaisingTracker(),
        artifacts=artifacts,
        inner=StubInner(artifacts=artifacts),
    )

    with pytest.raises(IssueError):
        await behavior.run(task())
