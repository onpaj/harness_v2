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

    `conflict_step` opts a single named step out of that canned shortcut: every
    time it runs, it actually drives git in `cwd` (the attached worktree) and
    performs the reviewer persona's own sync-with-base contract — `git fetch
    origin`, resolve the base branch, `git merge origin/<base>`, and on conflict
    capture the conflicting paths, `git merge --abort`, and return
    `REQUEST_CHANGES` — instead of taking the always-writes-a-canned-verdict
    path every other step uses. `touch_file`, when set, is edited by the
    `development` step so the task branch has a real local change that can
    collide with a divergent `origin` for that step's test scenario.
    """

    def __init__(self, conflict_step: str | None = None, touch_file: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._review_seen: set[str] = set()
        self._conflict_step = conflict_step
        self._touch_file = touch_file

    async def run(
        self, *, prompt: str, spec: AgentSpec, cwd: Path, timeout: float, on_output=None
    ) -> AgentRun:
        self.calls.append({"spec": spec, "cwd": cwd})

        match = _RELPATH.search(prompt)
        assert match is not None, "prompt does not contain an artifact path"
        relpath = match.group(0)
        task_id = match.group("task")
        target = Path(cwd) / relpath

        if spec.name == "development" and self._touch_file:
            (Path(cwd) / self._touch_file).write_text(
                "# project\n\nChanged by the task branch's development step.\n",
                encoding="utf-8",
            )

        if spec.name == self._conflict_step:
            return self._sync_with_base_then_verdict(cwd=Path(cwd), spec=spec, target=target)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {spec.name} artifact\n", encoding="utf-8")

        outcome = Outcome.DONE
        if spec.name == "review" and task_id not in self._review_seen:
            self._review_seen.add(task_id)
            outcome = Outcome.REQUEST_CHANGES
        assert outcome in spec.allowed_outcomes
        return AgentRun(outcome, summary=f"{spec.name}: done")

    @staticmethod
    def _sync_with_base_then_verdict(*, cwd: Path, spec: AgentSpec, target: Path) -> AgentRun:
        """Performs the reviewer persona's sync-with-base contract for real, on real git."""
        subprocess.run(["git", "-C", str(cwd), "fetch", "origin"], check=True, capture_output=True)

        base = "main"
        resolved = subprocess.run(
            ["git", "-C", str(cwd), "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True,
            text=True,
        )
        if resolved.returncode == 0:
            base = resolved.stdout.strip().removeprefix("refs/remotes/origin/")

        merge = subprocess.run(
            ["git", "-C", str(cwd), "merge", f"origin/{base}", "--no-edit"],
            capture_output=True,
            text=True,
        )
        target.parent.mkdir(parents=True, exist_ok=True)

        if merge.returncode != 0:
            conflicts = subprocess.run(
                ["git", "-C", str(cwd), "diff", "--name-only", "--diff-filter=U"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.split()
            subprocess.run(["git", "-C", str(cwd), "merge", "--abort"], check=True, capture_output=True)

            summary = (
                f"Merging origin/{base} produced conflicts in: {', '.join(conflicts)} "
                "— send back to development."
            )
            target.write_text(f"# {spec.name} artifact\n\n{summary}\n", encoding="utf-8")
            assert Outcome.REQUEST_CHANGES in spec.allowed_outcomes
            return AgentRun(Outcome.REQUEST_CHANGES, summary=summary)

        target.write_text(f"# {spec.name} artifact\n", encoding="utf-8")
        assert Outcome.DONE in spec.allowed_outcomes
        return AgentRun(Outcome.DONE, summary=f"{spec.name}: done")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _make_repo(path):
    path.mkdir(parents=True)
    # Explicit branch name: the conflict-scenario smoke test resolves the base
    # branch the same way the reviewer persona does (falling back to "main"),
    # so the fixture must not depend on the host's `init.defaultBranch`.
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("# project\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")

    # Landing now pushes the task branch before proposing a PR, so the fixture
    # needs somewhere to push to. A bare sibling repo stands in for the remote —
    # this keeps the smoke honest: a repo with no remote genuinely cannot land.
    remote = path.parent / (path.name + "-remote.git")
    _git(remote.parent, "init", "--bare", "-q", str(remote))
    _git(path, "remote", "add", "origin", str(remote))


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


def _find_task_file(root: Path, task_id: str) -> Path:
    matches = list(root.rglob(f"{task_id}.json"))
    assert matches, f"no task file for {task_id} under {root}"
    return matches[0]


async def test_review_syncs_with_base_and_requests_changes_on_real_conflict(tmp_path):
    """FR-3: a base branch that conflicts with the task branch sends `review`
    back to `development` with `request_changes`, on real git.

    The divergence is set up entirely before the harness starts: `origin` (the
    bare remote) gets a commit on `main` that edits `README.md`, while `repo`
    (what the worktree branches from) stays put — so the task branch and
    `origin/main` only actually diverge once `review` fetches, no timing
    coordination with the running harness is needed. `development`'s canned
    step (via `touch_file`) edits the same file locally, so the merge that
    `review` performs (per its persona's real contract, executed here by
    `EchoRunner`) genuinely conflicts.
    """
    root = tmp_path / "harness"
    repo = tmp_path / "repo"
    worktrees_root = tmp_path / "wt"
    _make_repo(repo)
    # `_make_repo` only adds the remote, it never pushes — publish the initial
    # commit first so the later divergent commit shares a common ancestor with
    # the task branch (otherwise the merge fails on unrelated histories rather
    # than producing a real conflict).
    _git(repo, "push", "-q", "origin", "main")

    remote = repo.parent / (repo.name + "-remote.git")
    clone = tmp_path / "origin-clone"
    _git(tmp_path, "clone", "-q", str(remote), str(clone))
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    (clone / "README.md").write_text("# project (renamed on main)\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", "rename on main")
    _git(clone, "push", "-q", "origin", "main")

    (root / "workflows").mkdir(parents=True)
    (root / "workflows" / "default.json").write_text(json.dumps(DEFINITION))
    task = Task(
        id="tsk_smoke_git_conflict",
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
        runner=EchoRunner(conflict_step="review", touch_file="README.md"),
        artifact_view=WorktreeArtifactView(worktrees_root),
        forge=FakeForge(root / "forge"),
        delay=0.0,
    )
    stop = asyncio.Event()
    background = asyncio.create_task(harness.run(poll_interval=0.01, stop=stop))

    worktree = worktrees_root / task_id
    review_artifact = worktree / f".artifacts/{task_id}/review-01.md"
    for _ in range(600):
        await asyncio.sleep(0.01)
        if review_artifact.exists():
            break
    # A short settle: the artifact write and the worker's commit are two
    # separate steps of the same behavior run — give the commit a beat to land.
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(background, timeout=RUNNER_TIMEOUT)

    assert review_artifact.exists(), "review-01.md was never written"
    content = review_artifact.read_text()
    assert "conflict" in content.lower()
    assert "README.md" in content
    assert "origin/main" in content

    # merge --abort ran: no merge in progress, no leftover conflict markers.
    merge_state = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--verify", "-q", "MERGE_HEAD"],
        capture_output=True,
    )
    assert merge_state.returncode != 0
    status = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status.strip() == ""

    # the verdict routed back to `development`, per the existing edge — not a
    # new outcome value, not a new route. The consumer records the outcome,
    # the dispatcher separately records where it routed to (invariant 3).
    task_file = _find_task_file(root, task_id)
    saved = Task.from_dict(json.loads(task_file.read_text()))
    consumed = [
        entry for entry in saved.history if entry.from_step == "review" and entry.outcome is not None
    ]
    assert consumed, "no history entry recorded for the review step's outcome"
    assert consumed[-1].outcome == "request_changes"
    routed = [
        entry
        for entry in saved.history
        if entry.from_step == "review" and entry.to_step == "development"
    ]
    assert routed, "review's request_changes never routed back to development"
