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
