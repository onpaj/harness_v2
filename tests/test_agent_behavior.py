from pathlib import Path

import pytest

from harness.behaviors.agent import ClaudeCliBehavior, compose_prompt
from harness.drivers.claude_cli import AgentError
from harness.drivers.memory import FakeAgentRunner, FakeClock, MemoryEventSink
from harness.models import BehaviorResult, Outcome, Task
from harness.ports.agent import AgentRun, AgentRunner, AgentSpec
from harness.ports.workspace import Workspace, WorkspaceHandle


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
):
    spec = spec or AgentSpec(name="development", prompt="you are a developer")
    workspace = RealFsWorkspace(tmp_path)
    behavior = ClaudeCliBehavior(
        clock=FakeClock(),
        workspace=workspace,
        runner=runner,
        spec=spec,
        events=events or MemoryEventSink(),
    )
    return behavior, workspace, spec


def dev_runner() -> FakeAgentRunner:
    return FakeAgentRunner(
        runs={"development": AgentRun(Outcome.DONE, "dev: done", raw="…")},
        writes={"development": {ARTIFACT_01: "# development\n\ncode\n"}},
    )


async def test_runs_agent_in_worktree_cwd_with_spec(tmp_path):
    runner = dev_runner()
    behavior, workspace, spec = build(tmp_path, runner=runner)

    await behavior.run(make_task())

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["cwd"] == workspace.handles["tsk_1"].path == tmp_path
    assert call["spec"] is spec
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

    assert result == BehaviorResult(Outcome.DONE, "dev: done")


async def test_emits_stage_output_events_tagged_with_identity(tmp_path):
    runner = FakeAgentRunner(
        runs={"development": AgentRun(Outcome.DONE, "dev: done")},
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
    spec = AgentSpec(
        name="reviewer",
        prompt="you are a reviewer",
        allowed_outcomes=(Outcome.DONE, Outcome.REQUEST_CHANGES),
    )
    prompt = compose_prompt(
        make_task(status="review"),
        step="review",
        artifact_relpath=".artifacts/tsk_1/review-01.md",
        spec=spec,
    )

    assert "review" in prompt
    assert "add rate limiting" in prompt
    assert ".artifacts/tsk_1/review-01.md" in prompt
    assert ".artifacts/tsk_1/" in prompt
    assert "done" in prompt and "request_changes" in prompt


def test_compose_prompt_demands_the_verdict_block_as_the_last_thing():
    # Fix B: the closing verdict must be stated as mandatory (a forgotten block
    # is what fails a finished run), and it must be the final thing in the prompt.
    spec = AgentSpec(
        name="development",
        prompt="you are dev",
        allowed_outcomes=(Outcome.DONE,),
    )
    prompt = compose_prompt(
        make_task(status="development"),
        step="development",
        artifact_relpath=".artifacts/tsk_1/development-01.md",
        spec=spec,
    )

    assert "```json" in prompt
    assert "must" in prompt.lower()
    assert prompt.rstrip().endswith("```")
