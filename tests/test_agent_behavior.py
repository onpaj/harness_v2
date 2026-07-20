from pathlib import Path

import pytest

from harness.behaviors.agent import ClaudeCliBehavior, compose_prompt
from harness.drivers.claude_cli import AgentError
from harness.drivers.memory import FakeAgentRunner, FakeClock
from harness.models import BehaviorResult, Outcome, Task
from harness.ports.agent import AgentRun, AgentRunner, AgentSpec
from harness.ports.workspace import Workspace, WorkspaceHandle


# --- Real-FS test double: handle nad skutečným tmp worktree, aby `next_attempt`
#     skenoval reálné soubory na disku (jako fáze 2 smoke). `write` zapisuje na
#     disk a `commit` jen zaznamená zprávu. -----------------------------------


class RealFsHandle(WorkspaceHandle):
    def __init__(self, task_id: str, root: Path) -> None:
        self._branch = f"harness/{task_id}"
        self._path = root
        self.writes: list[tuple[str, str]] = []
        self.commits: list[str] = []

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


class RealFsWorkspace(Workspace):
    """Opětovný attach téhož tasku vrací týž handle nad `root` (reálná cesta)."""

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
        data={"request": "přidej rate limiting"},
    )


ARTIFACT_01 = ".artifacts/tsk_1/development-01.md"


def build(tmp_path: Path, *, runner: AgentRunner, spec: AgentSpec | None = None):
    spec = spec or AgentSpec(name="development", prompt="jsi vývojář")
    workspace = RealFsWorkspace(tmp_path)
    behavior = ClaudeCliBehavior(
        clock=FakeClock(), workspace=workspace, runner=runner, spec=spec
    )
    return behavior, workspace, spec


def dev_runner() -> FakeAgentRunner:
    return FakeAgentRunner(
        runs={"development": AgentRun(Outcome.DONE, "dev: hotovo", raw="…")},
        writes={"development": {ARTIFACT_01: "# development\n\nkód\n"}},
    )


async def test_runs_agent_in_worktree_cwd_with_spec(tmp_path):
    runner = dev_runner()
    behavior, workspace, spec = build(tmp_path, runner=runner)

    await behavior.run(make_task())

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["cwd"] == workspace.handles["tsk_1"].path == tmp_path
    assert call["spec"] is spec
    assert call["timeout"] == 600.0


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
    assert workspace.handles["tsk_1"].commits == ["dev: hotovo"]


async def test_returns_behavior_result_from_agent_run(tmp_path):
    runner = dev_runner()
    behavior, _, _ = build(tmp_path, runner=runner)

    result = await behavior.run(make_task())

    assert result == BehaviorResult(Outcome.DONE, "dev: hotovo")


async def test_second_run_of_same_step_increments_attempt(tmp_path):
    runner = dev_runner()
    behavior, _, _ = build(tmp_path, runner=runner)

    await behavior.run(make_task())  # zapíše development-01.md na disk
    await behavior.run(make_task())  # skenuje reálné soubory → alokuje -02

    assert ARTIFACT_01 in runner.calls[0]["prompt"]
    assert ".artifacts/tsk_1/development-02.md" in runner.calls[1]["prompt"]


class RaisingRunner(AgentRunner):
    async def run(self, *, prompt, spec, cwd, timeout):
        raise AgentError("claude spadl")


async def test_runner_exception_propagates_and_skips_commit(tmp_path):
    behavior, workspace, _ = build(tmp_path, runner=RaisingRunner())

    with pytest.raises(AgentError):
        await behavior.run(make_task())

    # Nic se nespolklo a commit se nekonal — task půjde do failed/.
    assert workspace.handles["tsk_1"].commits == []


def test_compose_prompt_mentions_task_artifacts_and_allowed_outcomes():
    spec = AgentSpec(
        name="reviewer",
        prompt="jsi reviewer",
        allowed_outcomes=(Outcome.DONE, Outcome.REQUEST_CHANGES),
    )
    prompt = compose_prompt(
        make_task(status="review"),
        step="review",
        artifact_relpath=".artifacts/tsk_1/review-01.md",
        spec=spec,
    )

    assert "review" in prompt
    assert "přidej rate limiting" in prompt
    assert ".artifacts/tsk_1/review-01.md" in prompt
    assert ".artifacts/tsk_1/" in prompt
    assert "done" in prompt and "request_changes" in prompt
