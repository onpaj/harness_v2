from harness.behaviors.resolve_conflict import ResolveConflictBehavior
from harness.drivers.memory import FakeAgentRunner, FakeClock, MemoryEventSink, MemoryWorkspace
from harness.models import DONE, BehaviorResult, Task
from harness.ports.agent import AgentRun, AgentSpec


def make_task(branch: str = "harness/tsk_orig") -> Task:
    return Task(
        id="tsk_resolver_1",
        workflow_template="resolver",
        created="2026-07-21T10:00:00Z",
        repository="app-backend",
        status="resolve",
        data={
            "branch": branch,
            "title": "resolve merge conflict on PR #42",
            "source": {
                "kind": "mergeability",
                "repo": "o/r",
                "pr": 42,
                "url": "https://github.com/o/r/pull/42",
                "base": "main",
            },
        },
    )


def build(*, runner=None, spec=None):
    workspace = MemoryWorkspace()
    events = MemoryEventSink()
    spec = spec or AgentSpec(name="resolve", prompt="resolve the conflict")
    runner = runner or FakeAgentRunner(
        runs={"resolve": AgentRun(DONE, "resolve: fixed conflict")}
    )
    behavior = ResolveConflictBehavior(
        clock=FakeClock(), workspace=workspace, runner=runner, spec=spec, events=events
    )
    return behavior, workspace, runner, events


async def test_clean_merge_commits_without_calling_the_agent():
    behavior, workspace, runner, _ = build()
    task = make_task()

    result = await behavior.run(task)

    handle = workspace.handles[task.id]
    assert handle.merges == ["main"]
    assert runner.calls == []
    assert handle.commits == ["[resolve] merge main — no conflicts"]
    assert result.outcome == DONE
    assert "no conflicts" in result.summary


async def test_conflict_runs_the_agent_and_commits_its_summary():
    behavior, workspace, runner, _ = build()
    task = make_task()
    workspace.attach(task).conflicted = True

    result = await behavior.run(task)

    handle = workspace.handles[task.id]
    assert len(runner.calls) == 1
    assert handle.commits == ["resolve: fixed conflict"]
    assert result == BehaviorResult(DONE, "resolve: fixed conflict")


async def test_conflict_prompt_carries_attempt_relpath():
    behavior, workspace, runner, _ = build()
    task = make_task()
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    assert ".artifacts/tsk_resolver_1/resolve-01.md" in runner.calls[0]["prompt"]


async def test_attaches_the_prs_own_branch_not_a_fresh_one():
    behavior, workspace, _, _ = build()
    task = make_task(branch="harness/tsk_original")

    await behavior.run(task)

    assert workspace.handles[task.id].branch == "harness/tsk_original"


async def test_emits_stage_output_only_when_the_agent_actually_ran():
    behavior, workspace, _, events = build()
    task = make_task()
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    stage_output = [fields for name, fields in events.events if name == "stage_output"]
    assert all(fields["step"] == "resolve" for fields in stage_output)
