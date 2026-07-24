from dataclasses import replace
from pathlib import Path

import pytest

from harness.behaviors.agent import ClaudeCliBehavior, compose_prompt
from harness.drivers.claude_cli import AgentError
from harness.drivers.memory import FakeAgentRunner, FakeClock, MemoryEventSink
from harness.models import DONE, REQUEST_CHANGES, BehaviorResult, Task, Transition, Workflow
from harness.ports.agent import AgentRun, AgentRunner, AgentSpec
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository
from harness.ports.workspace import Workspace, WorkspaceHandle


class FakeWorkflowRepository(WorkflowRepository):
    """Minimal in-memory `WorkflowRepository` test double.

    Serves exactly the workflows handed to it by name; anything else raises
    `WorkflowNotFound`, mirroring `ServedWorkflowRepository`'s behavior for an
    unserved/unresolvable name.
    """

    def __init__(self, workflows: dict[str, Workflow]) -> None:
        self._workflows = workflows

    def get(self, name: str) -> Workflow:
        try:
            return self._workflows[name]
        except KeyError:
            raise WorkflowNotFound(name) from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._workflows))


# --- Real-FS test double: a handle over a real tmp worktree, so `next_attempt`
#     scans actual files on disk (like the phase 2 smoke). `write` writes to
#     disk and `commit` just records the message. ------------------------------


class RealFsHandle(WorkspaceHandle):
    def __init__(self, task_id: str, root: Path) -> None:
        self._branch = f"harness/{task_id}"
        self._path = root
        self.writes: list[tuple[str, str]] = []
        self.commits: list[str] = []
        self.pushes: list[str] = []

    @property
    def path(self) -> Path:
        return self._path

    @property
    def branch(self) -> str:
        return self._branch

    def write(self, relpath: str, content: str) -> None:
        target = self._path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.writes.append((relpath, content))

    def commit(self, message: str) -> str | None:
        self.commits.append(message)
        return f"sha{len(self.commits)}"

    def push(self) -> None:
        self.pushes.append(self._branch)

    def merge(self, base: str) -> bool:  # pragma: no cover - unused by these tests
        return False

    def abort_merge(self) -> None:  # pragma: no cover - unused by these tests
        pass


class RealFsWorkspace(Workspace):
    """Re-attaching the same task returns the same handle over `root` (real path)."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.handles: dict[str, RealFsHandle] = {}

    def attach(self, task: Task) -> RealFsHandle:
        handle = self.handles.get(task.id)
        if handle is None:
            handle = RealFsHandle(task.id, self._root)
            self.handles[task.id] = handle
        return handle


def make_task(status: str = "development") -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        repository="app-backend",
        status=status,
        data={"request": "add rate limiting"},
    )


ARTIFACT_01 = ".artifacts/tsk_1/development-01.md"


def build(
    tmp_path: Path,
    *,
    runner: AgentRunner,
    spec: AgentSpec | None = None,
    events: MemoryEventSink | None = None,
    workflows: WorkflowRepository | None = None,
):
    spec = spec or AgentSpec(name="development", prompt="you are a developer")
    workspace = RealFsWorkspace(tmp_path)
    behavior = ClaudeCliBehavior(
        clock=FakeClock(),
        workspace=workspace,
        runner=runner,
        spec=spec,
        events=events or MemoryEventSink(),
        workflows=workflows,
    )
    return behavior, workspace, spec


def dev_runner() -> FakeAgentRunner:
    return FakeAgentRunner(
        runs={"development": AgentRun(DONE, "dev: done", raw="…")},
        writes={"development": {ARTIFACT_01: "# development\n\ncode\n"}},
    )


async def test_runs_agent_in_worktree_cwd_with_spec(tmp_path):
    runner = dev_runner()
    behavior, workspace, spec = build(tmp_path, runner=runner)

    await behavior.run(make_task())

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["cwd"] == workspace.handles["tsk_1"].path == tmp_path
    # Workflow-less path (`workflows=None`): the effective spec handed to the
    # runner carries the same `allowed_outcomes` as `spec` — but it is always a
    # freshly `replace()`d instance, not `spec` itself, since Package C.
    assert call["spec"] == spec
    assert call["spec"].allowed_outcomes == spec.allowed_outcomes
    assert call["timeout"] == 1800.0


async def test_prompt_carries_attempt_relpath(tmp_path):
    runner = dev_runner()
    behavior, _, _ = build(tmp_path, runner=runner)

    await behavior.run(make_task())

    assert ARTIFACT_01 in runner.calls[0]["prompt"]


async def test_agent_artifact_written_and_committed_with_summary(tmp_path):
    runner = dev_runner()
    behavior, workspace, _ = build(tmp_path, runner=runner)

    await behavior.run(make_task())

    assert (tmp_path / ARTIFACT_01).exists()
    assert workspace.handles["tsk_1"].commits == ["dev: done"]


async def test_returns_behavior_result_from_agent_run(tmp_path):
    runner = dev_runner()
    behavior, _, _ = build(tmp_path, runner=runner)

    result = await behavior.run(make_task())

    assert result == BehaviorResult(DONE, "dev: done")


async def test_emits_stage_output_events_tagged_with_identity(tmp_path):
    runner = FakeAgentRunner(
        runs={"development": AgentRun(DONE, "dev: done")},
        outputs={"development": ["⏵ Bash: pytest -q", "all green"]},
    )
    events = MemoryEventSink()
    behavior, _, _ = build(tmp_path, runner=runner, events=events)

    await behavior.run(make_task())

    stage_output = [fields for name, fields in events.events if name == "stage_output"]
    assert [fields["line"] for fields in stage_output] == [
        "⏵ Bash: pytest -q",
        "all green",
    ]
    assert all(fields["task_id"] == "tsk_1" for fields in stage_output)
    assert all(fields["step"] == "development" for fields in stage_output)
    assert all(fields["attempt"] == 1 for fields in stage_output)
    # The stage_output events must NOT carry `task`/`queue` — else the board
    # projection would treat them as a column move (invariant 7).
    assert all("task" not in fields and "queue" not in fields for fields in stage_output)


async def test_second_run_of_same_step_increments_attempt(tmp_path):
    runner = dev_runner()
    behavior, _, _ = build(tmp_path, runner=runner)

    await behavior.run(make_task())  # writes development-01.md to disk
    await behavior.run(make_task())  # scans real files → allocates -02

    assert ARTIFACT_01 in runner.calls[0]["prompt"]
    assert ".artifacts/tsk_1/development-02.md" in runner.calls[1]["prompt"]


class RaisingRunner(AgentRunner):
    async def run(self, *, prompt, spec, cwd, timeout, on_output=None):
        raise AgentError("claude crashed")


async def test_runner_exception_propagates_and_skips_commit(tmp_path):
    behavior, workspace, _ = build(tmp_path, runner=RaisingRunner())

    with pytest.raises(AgentError):
        await behavior.run(make_task())

    # Nothing was swallowed and no commit happened — the task will go to failed/.
    assert workspace.handles["tsk_1"].commits == []


def test_compose_prompt_mentions_task_artifacts_and_allowed_outcomes():
    prompt = compose_prompt(
        make_task(status="review"),
        step="review",
        artifact_relpath=".artifacts/tsk_1/review-01.md",
        outcomes=(DONE, REQUEST_CHANGES),
        hints={},
    )

    assert "review" in prompt
    assert "add rate limiting" in prompt
    assert ".artifacts/tsk_1/review-01.md" in prompt
    assert ".artifacts/tsk_1/" in prompt
    assert "done" in prompt and "request_changes" in prompt


def test_compose_prompt_renders_hint_and_description():
    prompt = compose_prompt(
        make_task(status="review"),
        step="review",
        artifact_relpath=".artifacts/tsk_1/review-01.md",
        outcomes=(DONE, REQUEST_CHANGES),
        hints={REQUEST_CHANGES: "send it back for another pass"},
        description="Review the diff for correctness and style.",
    )

    assert "Review the diff for correctness and style." in prompt
    assert '"request_changes": send it back for another pass' in prompt
    # The concise machine-parseable format instruction survives unchanged.
    assert "<one of: done, request_changes>" in prompt


def _prompt_for(
    task: Task,
    *,
    outcomes: tuple[str, ...] = (DONE,),
    hints: dict[str, str] | None = None,
    description: str | None = None,
) -> str:
    return compose_prompt(
        task,
        step="development",
        artifact_relpath=ARTIFACT_01,
        outcomes=outcomes,
        hints=hints or {},
        description=description,
    )


def test_compose_prompt_includes_issue_body_when_present():
    task = replace(
        make_task(),
        data={"title": "add rate limiting", "body": "Repro: ...\nAC: ..."},
    )

    prompt = _prompt_for(task)

    assert "add rate limiting" in prompt
    assert "Repro: ...\nAC: ..." in prompt


def test_compose_prompt_unchanged_when_body_absent():
    with_body_absent = _prompt_for(make_task())

    assert with_body_absent == (
        "You are the agent for step 'development' of task tsk_1.\n"
        "Task: add rate limiting\n"
        "\n"
        "You'll find the context from previous steps as files in the "
        ".artifacts/tsk_1/ directory in your working directory — read them "
        "before you start.\n"
        "Write your output for this step to the file .artifacts/tsk_1/development-01.md.\n"
        "\n"
        "Finish by choosing exactly one outcome:\n"
        '  - "done"\n'
        "\n"
        "The harness reads your result by machine, not by eye. Your final "
        "message MUST end with exactly this fenced verdict block and nothing "
        "after it — not a prose summary, even once the artifact is written and "
        "the tests pass. A missing block fails the task:\n"
        "```json\n"
        '{"outcome": "<one of: done>", "summary": "<short summary>"}\n'
        "```"
    )


def test_compose_prompt_treats_whitespace_only_body_as_absent():
    task = replace(
        make_task(),
        data={"request": "add rate limiting", "body": "   \n  "},
    )

    assert _prompt_for(task) == _prompt_for(make_task())


def test_compose_prompt_does_not_duplicate_body_equal_to_request():
    task = replace(
        make_task(),
        data={"request": "add rate limiting", "body": "add rate limiting"},
    )

    assert _prompt_for(task) == _prompt_for(make_task())


def test_compose_prompt_demands_the_verdict_block_as_the_last_thing():
    # Fix B: the closing verdict must be stated as mandatory (a forgotten block
    # is what fails a finished run), and it must be the final thing in the prompt.
    prompt = compose_prompt(
        make_task(status="development"),
        step="development",
        artifact_relpath=".artifacts/tsk_1/development-01.md",
        outcomes=(DONE,),
        hints={},
    )

    assert "```json" in prompt
    assert "must" in prompt.lower()
    assert prompt.rstrip().endswith("```")


# --- ClaudeCliBehavior sourcing the outcome set live from the workflow ------


def make_workflow(
    *,
    name: str = "default",
    transitions: tuple[Transition, ...] = (),
    descriptions: dict[str, str] | None = None,
) -> Workflow:
    return Workflow(
        name=name,
        start="development",
        transitions=transitions,
        descriptions=descriptions or {},
    )


async def test_behavior_sources_outcomes_hints_description_from_workflow(tmp_path):
    workflow = make_workflow(
        transitions=(
            Transition(
                from_step="development",
                on=DONE,
                to_step="review",
                hint="the code is ready for review",
            ),
            Transition(
                from_step="development",
                on=REQUEST_CHANGES,
                to_step="development",
                hint="keep iterating, not ready yet",
            ),
        ),
        descriptions={"development": "Implement the requested change."},
    )
    workflows = FakeWorkflowRepository({"default": workflow})
    runner = FakeAgentRunner(
        runs={"development": AgentRun(DONE, "dev: done")},
    )
    spec = AgentSpec(
        name="development", prompt="you are a developer", allowed_outcomes=(DONE,)
    )
    behavior, _, _ = build(tmp_path, runner=runner, spec=spec, workflows=workflows)

    await behavior.run(make_task())

    call = runner.calls[0]
    # The runner's own enforcement (invariant #13) is fed the live,
    # workflow-derived set — not the frozen `spec.allowed_outcomes`.
    assert call["spec"].allowed_outcomes == workflow.outcomes_for("development")
    assert call["spec"].allowed_outcomes == (DONE, REQUEST_CHANGES)
    assert "Implement the requested change." in call["prompt"]
    assert "the code is ready for review" in call["prompt"]
    assert "keep iterating, not ready yet" in call["prompt"]


async def test_behavior_falls_back_to_spec_when_workflow_template_absent(tmp_path):
    workflow = make_workflow(
        transitions=(
            Transition(from_step="development", on=DONE, to_step="review"),
        ),
    )
    workflows = FakeWorkflowRepository({"default": workflow})
    runner = FakeAgentRunner(runs={"development": AgentRun(DONE, "dev: done")})
    spec = AgentSpec(
        name="development", prompt="you are a developer", allowed_outcomes=(DONE,)
    )
    behavior, _, _ = build(tmp_path, runner=runner, spec=spec, workflows=workflows)

    task = replace(make_task(), workflow_template=None)
    await behavior.run(task)

    call = runner.calls[0]
    assert call["spec"].allowed_outcomes == spec.allowed_outcomes


async def test_behavior_falls_back_to_spec_when_workflows_is_none(tmp_path):
    runner = FakeAgentRunner(runs={"development": AgentRun(DONE, "dev: done")})
    spec = AgentSpec(
        name="development", prompt="you are a developer", allowed_outcomes=(DONE,)
    )
    behavior, _, _ = build(tmp_path, runner=runner, spec=spec, workflows=None)

    await behavior.run(make_task())

    call = runner.calls[0]
    assert call["spec"].allowed_outcomes == spec.allowed_outcomes


async def test_behavior_falls_back_to_spec_when_step_has_no_outgoing_transitions(
    tmp_path,
):
    # `development` has no outgoing edge in this workflow (e.g. it's a
    # terminal step here) — an empty derived set must not reach the runner.
    workflow = make_workflow(
        transitions=(Transition(from_step="review", on=DONE, to_step="end"),),
    )
    workflows = FakeWorkflowRepository({"default": workflow})
    runner = FakeAgentRunner(runs={"development": AgentRun(DONE, "dev: done")})
    spec = AgentSpec(
        name="development",
        prompt="you are a developer",
        allowed_outcomes=(DONE, REQUEST_CHANGES),
    )
    behavior, _, _ = build(tmp_path, runner=runner, spec=spec, workflows=workflows)

    await behavior.run(make_task())

    call = runner.calls[0]
    assert call["spec"].allowed_outcomes == spec.allowed_outcomes


async def test_behavior_falls_back_to_spec_when_workflow_not_found(tmp_path):
    # `task.workflow_template` names a workflow the repository can't resolve
    # (e.g. not served) — defend rather than raise, same as the plan's
    # WorkflowNotFound guard.
    workflows = FakeWorkflowRepository({})
    runner = FakeAgentRunner(runs={"development": AgentRun(DONE, "dev: done")})
    spec = AgentSpec(
        name="development", prompt="you are a developer", allowed_outcomes=(DONE,)
    )
    behavior, _, _ = build(tmp_path, runner=runner, spec=spec, workflows=workflows)

    await behavior.run(make_task())

    call = runner.calls[0]
    assert call["spec"].allowed_outcomes == spec.allowed_outcomes
