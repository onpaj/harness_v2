"""UnblockPrBehavior — merge base, brief the agent, commit without artifacts."""

from __future__ import annotations

from harness.behaviors.unblock_pr import UnblockPrBehavior
from harness.drivers.memory import (
    FakeAgentRunner,
    FakeClock,
    MemoryEventSink,
    MemoryWorkspace,
)
from harness.models import DONE, BehaviorResult, Task
from harness.ports.agent import AgentRun, AgentSpec


def make_task(*, conflicted: bool, failing_checks: list) -> Task:
    return Task(
        id="tsk_unblock_1",
        workflow_template="unblock-pr",
        created="2026-07-31T10:00:00Z",
        repository="app-backend",
        status="unblock",
        data={
            "branch": "feature/x",
            "title": "unblock PR #42",
            "body": "the brief",
            "problem": {
                "conflicted": conflicted,
                "attempt": 1,
                "failing_checks": failing_checks,
            },
            "source": {
                "kind": "pull-request-health",
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
    spec = spec or AgentSpec(name="unblock", prompt="unblock the PR")
    runner = runner or FakeAgentRunner(
        runs={"unblock": AgentRun(DONE, "unblock: fixed it")}
    )
    behavior = UnblockPrBehavior(
        clock=FakeClock(), workspace=workspace, runner=runner, spec=spec, events=events
    )
    return behavior, workspace, runner, events


async def test_clean_merge_and_nothing_red_skips_the_agent():
    behavior, workspace, runner, _ = build()
    task = make_task(conflicted=True, failing_checks=[])

    result = await behavior.run(task)

    handle = workspace.handles[task.id]
    assert handle.merges == ["main"]
    assert runner.calls == []
    assert handle.commits == ["[unblock] merge main — nothing left to fix"]
    assert result.outcome == DONE
    assert "nothing to fix" in result.summary


async def test_clean_merge_but_red_checks_still_calls_the_agent():
    behavior, workspace, runner, _ = build()
    task = make_task(
        conflicted=False,
        failing_checks=[{"name": "pytest", "url": "u", "log_tail": "boom"}],
    )

    await behavior.run(task)

    assert len(runner.calls) == 1


async def test_real_conflict_calls_the_agent_and_commits_its_summary():
    behavior, workspace, runner, _ = build()
    task = make_task(conflicted=True, failing_checks=[])
    workspace.attach(task).conflicted = True

    result = await behavior.run(task)

    handle = workspace.handles[task.id]
    assert len(runner.calls) == 1
    assert handle.commits == ["unblock: fixed it"]
    assert result == BehaviorResult(DONE, "unblock: fixed it")


async def test_the_agent_commit_excludes_artifacts():
    behavior, workspace, runner, _ = build()
    task = make_task(conflicted=True, failing_checks=[])
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    handle = workspace.handles[task.id]
    assert handle.commit_excludes[-1] == (".artifacts",)


async def test_the_brief_reaches_the_prompt():
    behavior, workspace, runner, _ = build()
    task = make_task(conflicted=True, failing_checks=[])
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    prompt = runner.calls[0]["prompt"]
    assert "the brief" in prompt
    assert ".artifacts/tsk_unblock_1/unblock-01.md" in prompt
