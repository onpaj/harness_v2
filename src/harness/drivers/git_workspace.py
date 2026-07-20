"""Git worktree jako pracovní plocha.

`attach` připojí task k worktree pojmenovanému v tasku. Neexistuje-li, založí
ho přes `git worktree add` na task branchi `harness/<task_id>` z HEAD repa;
jinak reuse. Handle umí zapsat soubor a commitnout. Commit stageuje vše a při
prázdné pracovní ploše vrátí None místo prázdného commitu.

Volá systémový `git` přes subprocess — žádná nová produkční závislost.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from harness.models import Task
from harness.ports.workspace import Workspace, WorkspaceHandle

_IDENTITY = {
    "GIT_AUTHOR_NAME": "harness",
    "GIT_AUTHOR_EMAIL": "harness@local",
    "GIT_COMMITTER_NAME": "harness",
    "GIT_COMMITTER_EMAIL": "harness@local",
}


class GitError(RuntimeError):
    """Systémový `git` selhal. Nese příkaz i jeho stderr."""


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
            f"git {' '.join(args)} selhal (exit {error.returncode}): {error.stderr.strip()}"
        ) from error
    return result.stdout


class GitWorkspaceHandle(WorkspaceHandle):
    """Připojený git worktree."""

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


class GitWorkspace(Workspace):
    """Zakládá a znovupoužívá git worktree pojmenované v tasku."""

    def attach(self, task: Task) -> GitWorkspaceHandle:
        repo = Path(task.repository)
        worktree = Path(task.worktree)
        branch = f"harness/{task.id}"
        if not worktree.exists():
            _git(
                ["-C", str(repo), "worktree", "add", str(worktree), "-b", branch]
            )
        return GitWorkspaceHandle(worktree, branch)
