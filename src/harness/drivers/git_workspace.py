"""Git worktree as a working directory.

`attach` derives the repo root from the name (`task.repository`) via
`RepositoryRegistry` and places the worktree under `<worktrees_root>/<task_id>`.
If it does not exist, it creates it via `git worktree add` on the task branch
`harness/<task_id>` from the repo's HEAD; if it exists (reattach after a crash /
backward edge), it **resets** it back to HEAD and cleans up untracked files
(`reset --hard` + `clean -fd`, without `-x`), so the next attempt starts from a
clean slate. The handle can write a file and commit. Commit stages everything
and returns None instead of an empty commit when the working directory is empty.

Calls the system `git` via subprocess — no new production dependency.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from harness.models import Task
from harness.ports.repos import RepositoryRegistry
from harness.ports.workspace import Workspace, WorkspaceHandle

_IDENTITY = {
    "GIT_AUTHOR_NAME": "harness",
    "GIT_AUTHOR_EMAIL": "harness@local",
    "GIT_COMMITTER_NAME": "harness",
    "GIT_COMMITTER_EMAIL": "harness@local",
}


class GitError(RuntimeError):
    """The system `git` failed. Carries the command and its stderr."""


def _git(args: list[str], *, cwd: Path | None = None, env_extra: dict[str, str] | None = None) -> str:
    env = None
    if env_extra is not None:
        import os

        env = {**os.environ, **env_extra}
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        raise GitError(
            f"git {' '.join(args)} failed (exit {error.returncode}): {error.stderr.strip()}"
        ) from error
    return result.stdout


class GitWorkspaceHandle(WorkspaceHandle):
    """An attached git worktree."""

    def __init__(self, path: Path, branch: str) -> None:
        self._path = Path(path)
        self._branch = branch

    @property
    def path(self) -> Path:
        return self._path

    @property
    def branch(self) -> str:
        return self._branch

    def write(self, relpath: str, content: str) -> None:
        target = self._path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str | None:
        _git(["-C", str(self._path), "add", "-A"])
        status = _git(["-C", str(self._path), "status", "--porcelain"])
        if status.strip() == "":
            return None
        _git(
            ["-C", str(self._path), "commit", "-m", message],
            env_extra=_IDENTITY,
        )
        return _git(["-C", str(self._path), "rev-parse", "HEAD"]).strip()

    def push(self) -> None:
        # --force-with-lease, not --force: reset-on-reattach rewrites the task
        # branch on a re-run, so a plain push would be rejected as non-fast-
        # forward — but the lease still refuses to clobber a ref someone else
        # moved out from under us.
        _git(
            [
                "-C",
                str(self._path),
                "push",
                "--force-with-lease",
                "-u",
                "origin",
                self._branch,
            ]
        )


class GitWorkspace(Workspace):
    """Creates and reuses git worktrees under a shared root.

    Derives the repo root from the **name** (`task.repository`) via the registry
    — the task carries a logical name, not a path. The worktree lives at
    `<worktrees_root>/<task_id>`. If it does not exist, it creates it via
    `git worktree add`; on reattach a dirty worktree is reset to HEAD (so both a
    backward edge and a restart after a crash start from a clean slate).
    """

    def __init__(
        self,
        registry: RepositoryRegistry,
        worktrees_root: Path,
    ) -> None:
        self._registry = registry
        self._worktrees_root = Path(worktrees_root)

    def attach(self, task: Task) -> GitWorkspaceHandle:
        branch = f"harness/{task.id}"

        base = self._registry.resolve(task.repository)
        worktree = self._worktrees_root / task.id
        if not worktree.exists():
            worktree.parent.mkdir(parents=True, exist_ok=True)
            _git(
                ["-C", str(base), "worktree", "add", str(worktree), "-b", branch]
            )
        else:
            # Reset-on-reattach: both a backward edge and a restart after a
            # crash must start from a clean slate. `reset --hard` discards
            # uncommitted changes, `clean -fd` removes untracked files and
            # directories. Without `-x` — ignored files (e.g. `.artifacts/` if
            # they were gitignored) stay.
            _git(["-C", str(worktree), "reset", "--hard", "HEAD"])
            _git(["-C", str(worktree), "clean", "-fd"])
        return GitWorkspaceHandle(worktree, branch)
