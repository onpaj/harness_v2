"""Phase 3 smoke on real git and filesystem.

Like `test_smoke.py`, it polls with a real short `asyncio.sleep` — it's the only
place (alongside the phase-1 smoke) that verifies the git/fs/forge drivers live,
end-to-end. Don't tidy it into an in-memory form; that would remove the only
coverage of a real worktree.

Phase 3: the step's work is entrusted by `ClaudeCliBehavior` to a **real**
`AgentRunner` — here, though, not the real `claude` (which DOES NOT RUN, is
non-deterministic and expensive), but a local `EchoRunner` that parses the artifact
path out of the prompt and physically writes it into the worktree. Git, the
filesystem, and the forge are real. The artifacts must therefore end up versioned
in `.artifacts/<id>/` inside the worktree (not in a separate folder), the per-step
commits are carried by the task branch, and the PR is written to `prs.json`.
"""

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from harness.app import build
from harness.drivers.fake_forge import FakeForge
from harness.drivers.git_workspace import GitWorkspace
from harness.drivers.memory import MemoryAgentCatalog, MemoryRepositoryRegistry
from harness.drivers.system_clock import SystemClock
from harness.drivers.worktree_artifacts import WorktreeArtifactView
from harness.models import Outcome, Task
from harness.ports.agent import AgentRun, AgentRunner, AgentSpec

RUNNER_TIMEOUT = 5.0

DEFINITION = {
    "name": "default",
    "start": "plan",
    "transitions": [
        {"from": "plan", "on": "done", "to": "design"},
        {"from": "design", "on": "done", "to": "architecture"},
        {"from": "architecture", "on": "done", "to": "development"},
        {"from": "development", "on": "done", "to": "review"},
        {"from": "review", "on": "done", "to": "land"},
        {"from": "land", "on": "done", "to": "end"},
        {"from": "review", "on": "request_changes", "to": "development"},
    ],
}

# `.artifacts/<task_id>/<step>-<NN>.md`, the way `compose_prompt` inserts it into the prompt.
_RELPATH = re.compile(
    r"\.artifacts/(?P<task>[^/\s]+)/(?P<step>[^/\s]+)-(?P<nn>\d+)\.md"
)


class EchoRunner(AgentRunner):
    """Fake agent: writes the artifact to the path from the prompt and returns the step's verdict.

    `review` returns `REQUEST_CHANGES` on the first pass of a given task (the back
    edge to `development`), otherwise `DONE`. That way development and review both
    run twice and their artifacts get attempt 01 and 02. No subprocess, no `claude`.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._review_seen: set[str] = set()

    async def run(
        self, *, prompt: str, spec: AgentSpec, cwd: Path, timeout: float, on_output=None
    ) -> AgentRun:
        self.calls.append({"spec": spec, "cwd": cwd})

        match = _RELPATH.search(prompt)
        assert match is not None, "prompt does not contain an artifact path"
        relpath = match.group(0)
        task_id = match.group("task")
        target = Path(cwd) / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {spec.name} artifact\n", encoding="utf-8")

        outcome = Outcome.DONE
        if spec.name == "review" and task_id not in self._review_seen:
            self._review_seen.add(task_id)
            outcome = Outcome.REQUEST_CHANGES
        assert outcome in spec.allowed_outcomes
        return AgentRun(outcome, summary=f"{spec.name}: done")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_repo(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("# project\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")


def _catalog() -> MemoryAgentCatalog:
    def spec(step: str, *outcomes: Outcome) -> AgentSpec:
        return AgentSpec(
            name=step,
            prompt=f"Persona for the {step} step.",
            allowed_outcomes=outcomes or (Outcome.DONE,),
        )

    return MemoryAgentCatalog(
        {
            "plan": spec("plan"),
            "design": spec("design"),
            "architecture": spec("architecture"),
            "development": spec("development"),
            "review": spec("review", Outcome.DONE, Outcome.REQUEST_CHANGES),
        }
    )


async def test_task_lands_as_pull_request_on_real_git(tmp_path):
    root = tmp_path / "harness"
    repo = tmp_path / "repo"
    worktrees_root = tmp_path / "wt"
    _make_repo(repo)

    # Workflow on disk and a task in the inbox — no CLI, straight into the tree.
    (root / "workflows").mkdir(parents=True)
    (root / "workflows" / "default.json").write_text(json.dumps(DEFINITION))
    task = Task(
        id="tsk_smoke_git",
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
        repository="app",
        data={"title": "add rate limiting"},
    )
    (root / "tasks").mkdir(parents=True)
    (root / "tasks" / f"{task.id}.json").write_text(json.dumps(task.to_dict()))
    task_id = task.id

    registry = MemoryRepositoryRegistry({"app": repo})
    harness = build(
        root,
        "default",
        clock=SystemClock(),
        workspace=GitWorkspace(registry, worktrees_root),
        catalog=_catalog(),
        runner=EchoRunner(),
        artifact_view=WorktreeArtifactView(worktrees_root),
        forge=FakeForge(root / "forge"),
        delay=0.0,
    )
    stop = asyncio.Event()
    runner = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))
    for _ in range(600):
        await asyncio.sleep(0.01)
        if (root / "done" / f"{task_id}.json").exists():
            break
    stop.set()
    await asyncio.wait_for(runner, timeout=RUNNER_TIMEOUT)

    finished = Task.from_dict(
        json.loads((root / "done" / f"{task_id}.json").read_text())
    )
    assert finished.status == "end"

    # the worktree exists at the derived path and carries per-step commits on the task branch
    worktree = worktrees_root / task_id
    assert worktree.is_dir()
    branch = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == f"harness/{task_id}"
    log = subprocess.run(
        ["git", "-C", str(worktree), "log", "--oneline"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "plan: done" in log
    assert "development: done" in log
    assert "review: done" in log

    # the artifacts are IN THE WORKTREE under `.artifacts/<id>/`, versioned (git sees
    # them), not in a separate folder. The loop (request_changes) gave development and
    # review attempt 01 and 02.
    tracked = subprocess.run(
        ["git", "-C", str(worktree), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    for name in ("plan-01", "development-01", "development-02", "review-01", "review-02"):
        assert f".artifacts/{task_id}/{name}.md" in tracked
    # No separate artifacts folder outside the worktree.
    assert not (root / "artifacts").exists()

    # PR recorded in the forge
    prs = json.loads((root / "forge" / "prs.json").read_text())
    assert len(prs) == 1
    assert prs[0]["branch"] == f"harness/{task_id}"
