from harness.behaviors.landing import LandingBehavior
from harness.drivers.memory import (
    FakeClock,
    MemoryArtifactStore,
    MemoryForge,
    MemoryWorkspace,
)
from harness.models import HistoryEntry, Outcome, Task


def make_task() -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        repository="app-backend",
        worktree="/work/tsk_1",
        status="land",
        data={"title": "add rate limiting"},
        history=(
            HistoryEntry(
                at="t",
                actor="consumer:design",
                from_step="design",
                to_step=None,
                outcome="done",
                summary="design: done",
            ),
            HistoryEntry(
                at="t",
                actor="consumer:development",
                from_step="development",
                to_step=None,
                outcome="done",
                summary="development: added retry",
            ),
        ),
    )


def build():
    workspace = MemoryWorkspace()
    artifacts = MemoryArtifactStore()
    forge = MemoryForge()
    artifacts.begin("tsk_1", "design").put("design.md", "# design\n")
    artifacts.begin("tsk_1", "review").put("review.md", "# review\n")
    behavior = LandingBehavior(
        clock=FakeClock(), workspace=workspace, artifacts=artifacts, forge=forge
    )
    return behavior, workspace, artifacts, forge


async def test_opens_pull_request_for_task_branch():
    behavior, _, _, forge = build()

    result = await behavior.run(make_task())

    assert len(forge.opened) == 1
    assert forge.opened[0].branch == "harness/tsk_1"
    assert forge.opened[0].title == "add rate limiting"
    assert result.outcome is Outcome.DONE
    assert "PR" in result.summary


async def test_result_carries_structured_pr_identity():
    behavior, _, _, forge = build()

    result = await behavior.run(make_task())

    pull = forge.opened[0]
    assert result.data == {
        "pr": {
            "repo": pull.repo,
            "number": pull.number,
            "url": pull.url,
            "branch": pull.branch,
        }
    }


async def test_pr_body_aggregates_history_summaries():
    behavior, _, _, forge = build()

    await behavior.run(make_task())

    body = forge.bodies["harness/tsk_1"]
    assert "design: done" in body
    assert "development: added retry" in body


async def test_stages_artifacts_into_worktree():
    behavior, workspace, _, _ = build()

    await behavior.run(make_task())

    handle = workspace.handles["tsk_1"]
    staged = {relpath for relpath, _ in handle.writes}
    assert "docs/tasks/tsk_1/design/0/design.md" in staged
    assert "docs/tasks/tsk_1/review/0/review.md" in staged
    assert "[land] task artifacts" in handle.commits


async def test_landing_is_idempotent():
    behavior, _, _, forge = build()
    task = make_task()

    await behavior.run(task)
    await behavior.run(task)

    assert len(forge.opened) == 1


async def test_copy_artifacts_false_skips_copy_but_opens_pr():
    workspace = MemoryWorkspace()
    artifacts = MemoryArtifactStore()
    forge = MemoryForge()
    artifacts.begin("tsk_1", "design").put("design.md", "# design\n")
    behavior = LandingBehavior(
        clock=FakeClock(),
        workspace=workspace,
        artifacts=artifacts,
        forge=forge,
        copy_artifacts=False,
    )

    result = await behavior.run(make_task())

    handle = workspace.handles["tsk_1"]
    assert handle.writes == []
    assert "[land] task artifacts" not in handle.commits
    assert len(forge.opened) == 1
    assert result.outcome is Outcome.DONE


async def test_pushes_the_branch_before_opening_the_pull_request():
    behavior, workspace, _, forge = build()

    await behavior.run(make_task())

    handle = workspace.handles["tsk_1"]
    assert handle.pushes == ["harness/tsk_1"]
    assert len(forge.opened) == 1


async def test_merges_the_base_branch_before_pushing():
    """Pre-landing sync: landing merges the PR's base branch into the task
    branch (a clean merge is committed) so the PR is born up-to-date with base."""
    behavior, workspace, _, forge = build()

    await behavior.run(make_task())

    handle = workspace.handles["tsk_1"]
    # The base merged is the very branch the forge opens the PR against.
    assert handle.merges == [forge.base_branch(make_task())]
    # A clean merge is committed onto the task branch, then pushed.
    assert "[land] merge main" in handle.commits
    assert handle.merge_aborts == 0
    assert handle.pushes == ["harness/tsk_1"]


async def test_clean_merge_summary_is_not_flagged():
    behavior, _, _, forge = build()

    result = await behavior.run(make_task())

    assert result.outcome is Outcome.DONE
    assert "conflicts with" not in result.summary


async def test_conflict_aborts_the_merge_but_still_opens_a_flagged_pr():
    """A base merge that conflicts is abandoned (landing has no agent to
    resolve it); the PR opens on the un-merged branch anyway, flagged, so the
    resolver workflow reconciles it downstream."""
    behavior, workspace, _, forge = build()
    workspace.attach(make_task()).conflicted = True

    result = await behavior.run(make_task())

    handle = workspace.handles["tsk_1"]
    assert handle.merges == ["main"]
    assert handle.merge_aborts == 1
    # The conflicted merge is never committed onto the branch.
    assert "[land] merge main" not in handle.commits
    # The PR still opens, and the summary flags the conflict for the resolver.
    assert len(forge.opened) == 1
    assert handle.pushes == ["harness/tsk_1"]
    assert result.outcome is Outcome.DONE
    assert "conflicts with main" in result.summary


async def test_no_pull_request_when_the_push_fails():
    """A branch the remote never received must not get a PR claiming it exists."""
    import pytest

    behavior, workspace, _, forge = build()

    class Boom(RuntimeError):
        pass

    def explode():
        raise Boom("remote rejected")

    workspace.attach(make_task()).push = explode

    with pytest.raises(Boom):
        await behavior.run(make_task())

    assert forge.opened == []
