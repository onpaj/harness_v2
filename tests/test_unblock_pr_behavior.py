"""UnblockPrBehavior — merge base, brief the agent, commit without artifacts."""

from __future__ import annotations

from harness.behaviors.unblock_pr import UnblockPrBehavior
from harness.drivers.memory import (
    FakeAgentRunner,
    FakeClock,
    MemoryEventSink,
    MemoryWorkflowRepository,
    MemoryWorkspace,
)
from harness.models import DONE, BehaviorResult, Task, Transition, Workflow
from harness.ports.agent import AgentRun, AgentSpec

STUCK = "stuck"


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


def build(*, runner=None, spec=None, workflows=None):
    workspace = MemoryWorkspace()
    events = MemoryEventSink()
    spec = spec or AgentSpec(name="unblock", prompt="unblock the PR")
    runner = runner or FakeAgentRunner(
        runs={"unblock": AgentRun(DONE, "unblock: fixed it")}
    )
    behavior = UnblockPrBehavior(
        clock=FakeClock(),
        workspace=workspace,
        runner=runner,
        spec=spec,
        events=events,
        workflows=workflows,
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


# --- the brief is stale: base moved between the scan and this run ------------


async def test_a_conflict_the_brief_never_mentioned_is_added_to_the_prompt():
    """The check saw no conflict (only a red check-run); the base advanced
    before this task ran and the merge conflicts now. The agent must be told,
    or it fixes the test it was told about and the worker commits the markers."""
    behavior, workspace, runner, _ = build()
    task = make_task(
        conflicted=False,
        failing_checks=[{"name": "pytest", "url": "u", "log_tail": "boom"}],
    )
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    prompt = runner.calls[0]["prompt"]
    assert "**The branch conflicts with its base.**" in prompt
    assert "conflict markers are in the files in front of you" in prompt
    # The brief the check did render is still there — this is an addition.
    assert "the brief" in prompt


async def test_a_conflict_that_resolved_itself_is_corrected_in_the_prompt():
    """The mirror case: the check saw `dirty`, the merge is clean now. The
    brief tells the agent to look for markers that do not exist."""
    behavior, workspace, runner, _ = build()
    task = make_task(
        conflicted=True,
        failing_checks=[{"name": "pytest", "url": "u", "log_tail": "boom"}],
    )

    await behavior.run(task)

    prompt = runner.calls[0]["prompt"]
    assert "conflicts with its base — it no longer does" in prompt
    assert "no conflict markers to look for" in prompt


async def test_an_agreeing_brief_is_left_alone():
    behavior, workspace, runner, _ = build()
    task = make_task(
        conflicted=True,
        failing_checks=[{"name": "pytest", "url": "u", "log_tail": "boom"}],
    )
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    prompt = runner.calls[0]["prompt"]
    assert "**The branch conflicts with its base.**" not in prompt
    assert "it no longer does" not in prompt


# --- a give-up outcome pushes nothing ---------------------------------------


async def test_a_non_done_outcome_abandons_the_conflicted_merge():
    """`stuck` means "I could not fix this — push nothing". Committing anyway
    turns unresolved markers into a two-parent merge commit on the branch."""
    runner = FakeAgentRunner(runs={"unblock": AgentRun(STUCK, "unblock: gave up")})
    behavior, workspace, runner, _ = build(runner=runner)
    task = make_task(conflicted=True, failing_checks=[])
    workspace.attach(task).conflicted = True

    result = await behavior.run(task)

    handle = workspace.handles[task.id]
    assert handle.merge_aborts == 1
    assert handle.commits == []
    assert result == BehaviorResult(STUCK, "unblock: gave up")


async def test_a_done_outcome_still_commits_the_conflicted_merge():
    behavior, workspace, runner, _ = build()
    task = make_task(conflicted=True, failing_checks=[])
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    handle = workspace.handles[task.id]
    assert handle.merge_aborts == 0
    assert handle.commits == ["unblock: fixed it"]


async def test_a_non_done_outcome_without_a_conflict_still_commits():
    """Nothing to abort — the base merge was clean, and the branch is the
    agent's own work in progress. Only a conflicted merge is abandoned."""
    runner = FakeAgentRunner(runs={"unblock": AgentRun(STUCK, "unblock: gave up")})
    behavior, workspace, runner, _ = build(runner=runner)
    task = make_task(
        conflicted=False,
        failing_checks=[{"name": "pytest", "url": "u", "log_tail": "boom"}],
    )

    await behavior.run(task)

    handle = workspace.handles[task.id]
    assert handle.merge_aborts == 0
    assert handle.commits == ["unblock: gave up"]


# --- the workflow owns the step's vocabulary (invariant 42) ------------------


UNBLOCK_WORKFLOW = Workflow(
    name="unblock-pr",
    start="unblock",
    transitions=(
        Transition(
            from_step="unblock",
            on=DONE,
            to_step="land",
            hint="the conflict is resolved and/or the failing checks should now pass",
        ),
        Transition(
            from_step="unblock",
            on=STUCK,
            to_step="end",
            hint="you could not fix this from what you were given — push nothing",
        ),
        Transition(from_step="land", on=DONE, to_step="end"),
    ),
    descriptions={"unblock": "fix whatever is blocking this pull request"},
)


async def test_the_workflow_hints_and_description_reach_the_prompt():
    workflows = MemoryWorkflowRepository({"unblock-pr": UNBLOCK_WORKFLOW})
    spec = AgentSpec(name="unblock", prompt="unblock the PR", allowed_outcomes=(DONE,))
    behavior, workspace, runner, _ = build(spec=spec, workflows=workflows)
    task = make_task(conflicted=True, failing_checks=[])
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    call = runner.calls[0]
    assert "fix whatever is blocking this pull request" in call["prompt"]
    assert "you could not fix this from what you were given" in call["prompt"]
    # The live vocabulary binds the runner's own verdict check too, exactly as
    # it does for `ClaudeCliBehavior` (invariant 42).
    assert call["spec"].allowed_outcomes == (DONE, STUCK)


async def test_falls_back_to_the_persona_without_a_workflow_repository():
    spec = AgentSpec(
        name="unblock", prompt="unblock the PR", allowed_outcomes=(DONE, STUCK)
    )
    behavior, workspace, runner, _ = build(spec=spec, workflows=None)
    task = make_task(conflicted=True, failing_checks=[])
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    assert runner.calls[0]["spec"].allowed_outcomes == (DONE, STUCK)


# --- a give-up is told to a human (ADR-0026) --------------------------------


def build_with_labeller(*, runner=None, labeller=None):
    calls: list[tuple[str, int, str]] = []

    def record(repo: str, number: int, label: str) -> None:
        calls.append((repo, number, label))

    workspace = MemoryWorkspace()
    behavior = UnblockPrBehavior(
        clock=FakeClock(),
        workspace=workspace,
        runner=runner
        or FakeAgentRunner(runs={"unblock": AgentRun(DONE, "unblock: fixed it")}),
        spec=AgentSpec(name="unblock", prompt="unblock the PR"),
        events=MemoryEventSink(),
        pr_labeller=labeller if labeller is not None else record,
    )
    return behavior, workspace, calls


async def test_a_give_up_labels_the_pull_request_for_a_human():
    """`stuck` never moves the head sha, so the check's attempt budget can
    never end this PR: without the label it carries `harness:autofix-1@<sha>`,
    no human is told, and the task is re-minted every retention window."""
    runner = FakeAgentRunner(runs={"unblock": AgentRun(STUCK, "unblock: gave up")})
    behavior, workspace, calls = build_with_labeller(runner=runner)
    task = make_task(conflicted=True, failing_checks=[])
    task.data["give_up_label"] = "harness:needs-human"
    workspace.attach(task).conflicted = True

    result = await behavior.run(task)

    assert calls == [("o/r", 42, "harness:needs-human")]
    assert result.outcome == STUCK


async def test_the_label_applied_is_the_one_the_check_configured():
    runner = FakeAgentRunner(runs={"unblock": AgentRun(STUCK, "unblock: gave up")})
    behavior, workspace, calls = build_with_labeller(runner=runner)
    task = make_task(conflicted=True, failing_checks=[])
    task.data["give_up_label"] = "team:needs-human"
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    assert calls == [("o/r", 42, "team:needs-human")]


async def test_a_done_outcome_labels_nothing():
    behavior, workspace, calls = build_with_labeller()
    task = make_task(conflicted=True, failing_checks=[])
    task.data["give_up_label"] = "harness:needs-human"
    workspace.attach(task).conflicted = True

    await behavior.run(task)

    assert calls == []


async def test_the_clean_merge_shortcut_labels_nothing():
    """Nothing was wrong by the time the task ran — that is a success, not a
    give-up, and the agent was never even called."""
    behavior, workspace, calls = build_with_labeller()
    task = make_task(conflicted=True, failing_checks=[])
    task.data["give_up_label"] = "harness:needs-human"

    await behavior.run(task)

    assert calls == []


async def test_a_task_carrying_no_give_up_label_is_not_labelled():
    """A hand-submitted task routed to `unblock` carries no label to apply —
    a no-op, never a guess and never an exception."""
    runner = FakeAgentRunner(runs={"unblock": AgentRun(STUCK, "unblock: gave up")})
    behavior, workspace, calls = build_with_labeller(runner=runner)
    task = make_task(conflicted=True, failing_checks=[])
    workspace.attach(task).conflicted = True

    result = await behavior.run(task)

    assert calls == []
    assert result.outcome == STUCK


async def test_a_failing_label_call_notes_itself_instead_of_failing_the_task():
    """The agent's verdict is real work already done; a 5xx on one label call
    must not turn it into a failed task. The next tick re-mints and retries."""

    def explode(repo, number, label):
        raise RuntimeError("502 Bad Gateway")

    runner = FakeAgentRunner(runs={"unblock": AgentRun(STUCK, "unblock: gave up")})
    behavior, workspace, _ = build_with_labeller(runner=runner, labeller=explode)
    task = make_task(conflicted=True, failing_checks=[])
    task.data["give_up_label"] = "harness:needs-human"
    workspace.attach(task).conflicted = True

    result = await behavior.run(task)

    assert result.outcome == STUCK
    assert "harness:needs-human" in result.summary
    assert "502 Bad Gateway" in result.summary


async def test_a_give_up_without_a_labeller_wired_is_still_a_clean_give_up():
    """No `GITHUB_TOKEN`, no labeller — and no crash. The check that would
    re-mint the task is gated on the same token, so nothing is re-minted."""
    runner = FakeAgentRunner(runs={"unblock": AgentRun(STUCK, "unblock: gave up")})
    behavior, workspace, _runner, _ = build(runner=runner)
    task = make_task(conflicted=True, failing_checks=[])
    task.data["give_up_label"] = "harness:needs-human"
    workspace.attach(task).conflicted = True

    result = await behavior.run(task)

    assert result == BehaviorResult(STUCK, "unblock: gave up")


# --- a malformed task says which key is missing ------------------------------


async def test_a_task_with_no_base_branch_raises_a_named_error():
    """Every task the check mints carries `data.source.base`. A hand-submitted
    or hand-edited one routed to this step must fail with a message an
    operator can act on, not a bare `KeyError: 'base'`."""
    import pytest

    from harness.behaviors.unblock_pr import UnblockPrError

    behavior, workspace, _runner, _ = build()
    task = make_task(conflicted=True, failing_checks=[])
    del task.data["source"]["base"]

    with pytest.raises(UnblockPrError) as error:
        await behavior.run(task)

    assert "base" in str(error.value)
    assert task.id in str(error.value)


async def test_a_task_with_no_source_at_all_raises_the_same_named_error():
    import pytest

    from harness.behaviors.unblock_pr import UnblockPrError

    behavior, workspace, _runner, _ = build()
    task = make_task(conflicted=True, failing_checks=[])
    del task.data["source"]

    with pytest.raises(UnblockPrError):
        await behavior.run(task)
