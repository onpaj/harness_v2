"""Git worktree jako pracovní plocha.

`attach` odvodí kořen repa z jména (`task.repository`) přes `RepositoryRegistry`
a worktree umístí pod `<worktrees_root>/<task_id>`. Neexistuje-li, založí ho
přes `git worktree add` na task branchi `harness/<task_id>` z HEAD repa;
existuje-li (reattach po pádu / zpětné hraně), **resetne** ho zpět na HEAD a
uklidí netrackované soubory (`reset --hard` + `clean -fd`, bez `-x`), aby další
pokus začal z čistého listu. Handle umí zapsat soubor a commitnout. Commit
stageuje vše a při prázdné pracovní ploše vrátí None místo prázdného commitu.

Volá systémový `git` přes subprocess — žádná nová produkční závislost.
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


def git_remote_url(path: Path, remote: str = "origin") -> str | None:
    """URL remotu repa v `path` (default `origin`) — původ repa venku (GitHub).

    Zdroj repa se nedrží zvlášť v configu; je už tady, v checkoutu. Chybí-li
    remote (nebo `path` není git repo), vrať `None` — volající to přeloží na
    „zdroj vypnut", ne na pád."""
    try:
        url = _git(["-C", str(path), "remote", "get-url", remote])
    except GitError:
        return None
    return url.strip() or None


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
    """Zakládá a znovupoužívá git worktree pod společným kořenem.

    Kořen repa odvozuje z **jména** (`task.repository`) přes registry — task
    nese logické jméno, ne cestu. Worktree leží na `<worktrees_root>/<task_id>`.
    Neexistuje-li, založí ho přes `git worktree add`; reattach špinavý worktree
    resetuje na HEAD (zpětná hrana i restart po pádu tak začnou z čistého listu).
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
            # Reset-on-reattach: zpětná hrana i restart po pádu musí začít
            # z čistého listu. `reset --hard` zahodí necommitnuté změny, `clean
            # -fd` smaže netrackované soubory a adresáře. Bez `-x` — ignorované
            # soubory (např. `.artifacts/` pokud by byly gitignored) zůstávají.
            _git(["-C", str(worktree), "reset", "--hard", "HEAD"])
            _git(["-C", str(worktree), "clean", "-fd"])
        return GitWorkspaceHandle(worktree, branch)
