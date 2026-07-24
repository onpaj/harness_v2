"""`OpenIssueBehavior` — the `open-issue` finisher kind (ADR-0018).

Migrated from the old `Healer._heal`'s issue-filing half in
`tests/test_healer.py`: idempotency by marker, an `IssueError` propagating
(never swallowed — the recursion guard, not in-behavior handling, is what
stops a loop), and the title/body construction.
"""

import pytest

from harness.behaviors.open_issue import OpenIssueBehavior
from harness.drivers.memory import FakeClock, MemoryArtifactStore, MemoryIssueTracker
from harness.models import DONE, HistoryEntry, Task
from harness.ports.issues import IssueError, IssueTracker


def heal_task(
    task_id: str = "tsk_file_issue",
    *,
    of: str = "tsk_boom",
    data: dict | None = None,
    history: tuple = (),
) -> Task:
    base_data = {
        "heal": {"of": of},
        "original_request": "Do the thing",
    }
    if data:
        base_data.update(data)
    return Task(
        id=task_id,
        workflow_template="heal",
        created="2026-07-21T10:00:00Z",
        status="file-issue",
        data=base_data,
        history=history,
    )


def make_behavior(*, tracker, artifacts, repo="onpaj/harness_v2") -> OpenIssueBehavior:
    return OpenIssueBehavior(
        tracker=tracker, repo=repo, artifacts=artifacts, clock=FakeClock()
    )


async def test_files_an_issue_from_the_heal_artifact_and_settles_done():
    artifacts = MemoryArtifactStore()
    artifacts.begin("tsk_file_issue", "heal").put(
        "heal-01.md", "# Fix the driver\n\nthe driver mis-handles X"
    )
    tracker = MemoryIssueTracker()
    behavior = make_behavior(tracker=tracker, artifacts=artifacts)

    result = await behavior.run(heal_task())

    assert result.outcome == DONE
    assert len(tracker.opened) == 1
    opened = tracker.opened[0]
    assert opened["repo"] == "onpaj/harness_v2"
    assert opened["marker"] == "tsk_boom"  # the *original* failed task's id
    assert opened["title"] == "Fix the driver"  # from the '# ' heading


async def test_second_heal_of_the_same_marker_returns_the_existing_issue():
    tracker = MemoryIssueTracker()
    for task_id in ("tsk_attempt_1", "tsk_attempt_2"):
        artifacts = MemoryArtifactStore()
        artifacts.begin(task_id, "heal").put("heal-01.md", "# Fix it\n\nbody")
        behavior = make_behavior(tracker=tracker, artifacts=artifacts)
        await behavior.run(heal_task(task_id, of="tsk_boom"))

    assert len(tracker.opened) == 1  # marker dedup held across both


async def test_issue_error_propagates_uncaught():
    class RaisingTracker(IssueTracker):
        def open_issue(self, repo, *, title, body, labels, marker):
            raise IssueError("no token")

    artifacts = MemoryArtifactStore()
    artifacts.begin("tsk_file_issue", "heal").put("heal-01.md", "# Fix it\n\nbody")
    behavior = make_behavior(tracker=RaisingTracker(), artifacts=artifacts)

    with pytest.raises(IssueError):
        await behavior.run(heal_task())


async def test_title_falls_back_to_the_verdict_summary_when_no_heading():
    artifacts = MemoryArtifactStore()
    artifacts.begin("tsk_file_issue", "heal").put("heal-01.md", "no heading here")
    tracker = MemoryIssueTracker()
    behavior = make_behavior(tracker=tracker, artifacts=artifacts)
    history = (
        HistoryEntry(
            at="2026-07-21T10:00:00Z",
            actor="consumer:heal",
            from_step="heal",
            to_step=None,
            outcome="done",
            summary="Add the missing edge",
        ),
    )

    await behavior.run(heal_task(history=history))

    assert tracker.opened[0]["title"] == "Add the missing edge"


async def test_title_falls_back_to_original_request_when_no_heading_or_summary():
    artifacts = MemoryArtifactStore()
    artifacts.begin("tsk_file_issue", "heal").put("heal-01.md", "no heading here")
    tracker = MemoryIssueTracker()
    behavior = make_behavior(tracker=tracker, artifacts=artifacts)

    await behavior.run(heal_task())

    assert tracker.opened[0]["title"] == "Self-heal: Do the thing"


async def test_body_includes_an_origin_line_when_source_is_present():
    artifacts = MemoryArtifactStore()
    artifacts.begin("tsk_file_issue", "heal").put("heal-01.md", "# Title\n\nbody")
    tracker = MemoryIssueTracker()
    behavior = make_behavior(tracker=tracker, artifacts=artifacts)

    await behavior.run(heal_task(data={"source": {"url": "https://gh/i/42"}}))

    assert "Origin: https://gh/i/42" in tracker.opened[0]["body"]


async def test_body_has_no_origin_line_when_source_is_absent():
    artifacts = MemoryArtifactStore()
    artifacts.begin("tsk_file_issue", "heal").put("heal-01.md", "# Title\n\nbody")
    tracker = MemoryIssueTracker()
    behavior = make_behavior(tracker=tracker, artifacts=artifacts)

    await behavior.run(heal_task())

    assert "Origin:" not in tracker.opened[0]["body"]


async def test_missing_draft_still_files_a_generic_issue():
    artifacts = MemoryArtifactStore()  # nothing written for "heal"
    tracker = MemoryIssueTracker()
    behavior = make_behavior(tracker=tracker, artifacts=artifacts)

    result = await behavior.run(heal_task())

    assert result.outcome == DONE
    assert "tsk_boom" in tracker.opened[0]["body"]
