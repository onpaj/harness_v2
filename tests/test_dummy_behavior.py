from dataclasses import replace

from harness.drivers.dummy_behavior import DummyBehavior
from harness.drivers.memory import FakeClock, MemoryArtifactStore, MemoryWorkspace
from harness.models import DONE, REQUEST_CHANGES, Task


def make_task(status: str, task_id: str = "tsk_1") -> Task:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        repository="app-backend",
        worktree="/work/tsk_1",
        status=status,
    )


def build(**kwargs) -> tuple[DummyBehavior, MemoryWorkspace, MemoryArtifactStore]:
    workspace = MemoryWorkspace()
    artifacts = MemoryArtifactStore()
    behavior = DummyBehavior(
        clock=FakeClock(), workspace=workspace, artifacts=artifacts, **kwargs
    )
    return behavior, workspace, artifacts


async def test_returns_done_and_waits():
    workspace = MemoryWorkspace()
    artifacts = MemoryArtifactStore()
    clock = FakeClock()
    behavior = DummyBehavior(
        clock=clock, workspace=workspace, artifacts=artifacts, delay=5.0
    )

    result = await behavior.run(make_task("design"))

    assert result.outcome == DONE
    assert result.summary
    assert clock.slept == [5.0]


async def test_writes_artifact_for_the_step():
    behavior, _, artifacts = build(delay=0.0)

    await behavior.run(make_task("design"))

    assert artifacts.read("tsk_1", "design", 0, "design.md") is not None


async def test_commits_work_with_step_prefixed_message():
    behavior, workspace, _ = build(delay=0.0)

    await behavior.run(make_task("development"))

    handle = workspace.handles["tsk_1"]
    assert handle.commits == ["[development] development: done"]
    assert handle.writes  # something was written to the worktree


async def test_configured_step_asks_for_changes_only_once():
    behavior, _, _ = build(delay=0.0, request_changes_once_at="review")
    task = make_task("review")

    assert (await behavior.run(task)).outcome == REQUEST_CHANGES
    assert (await behavior.run(task)).outcome == DONE
    assert (await behavior.run(task)).outcome == DONE


async def test_loop_produces_second_attempt():
    """A second pass through the same step = attempt 1, does not overwrite attempt 0."""
    behavior, _, artifacts = build(delay=0.0)
    task = make_task("development")

    await behavior.run(task)
    await behavior.run(task)

    assert artifacts.read("tsk_1", "development", 0, "development.md") is not None
    assert artifacts.read("tsk_1", "development", 1, "development.md") is not None


async def test_other_steps_are_unaffected():
    behavior, _, _ = build(delay=0.0, request_changes_once_at="review")

    assert (await behavior.run(make_task("design"))).outcome == DONE


async def test_request_changes_is_per_task():
    behavior, _, _ = build(delay=0.0, request_changes_once_at="review")
    first = make_task("review")
    second = replace(first, id="tsk_2")

    assert (await behavior.run(first)).outcome == REQUEST_CHANGES
    assert (await behavior.run(second)).outcome == REQUEST_CHANGES
    assert (await behavior.run(first)).outcome == DONE


async def test_dummy_writes_into_the_versioned_artifacts_dir():
    """`.harness/` is gitignored in real repos, so a dummy writing there commits
    nothing and the task branch reaches landing with no diff — GitHub then
    refuses the PR with a 422. Write where the real agent writes (invariant 16)."""
    from harness.drivers.memory import MemoryArtifactStore, MemoryWorkspace

    workspace = MemoryWorkspace()
    behavior = DummyBehavior(
        clock=FakeClock(), workspace=workspace, artifacts=MemoryArtifactStore(), delay=0
    )
    task = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        repository="app",
        status="plan",
    )

    await behavior.run(task)

    written = [path for path, _ in workspace.handles["tsk_1"].writes]
    # The attempt index is the store's business; the *location* is the point.
    assert len(written) == 1
    assert written[0].startswith(".artifacts/tsk_1/plan-")
    assert written[0].endswith(".md")
    assert not any(p.startswith(".harness/") for p in written)
