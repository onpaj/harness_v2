"""Per-run git worktree lifecycle.

Worktrees are added directly off the **bare mirror**, so the run branch lives in
the mirror's own `refs/heads/` from the moment `git worktree add` returns. That
is why there is no push step anywhere in this module: a commit made inside a
worktree is immediately visible to `resolve_ref(mirror, branch)`. Chaining a
second worktree onto a previous run's output SHA is how artifacts are inherited
from one run to the next.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from agentharness.config import Config

from .mirror import branch_exists, git


def _identity(cfg: Config) -> tuple[str, ...]:
    return (
        "-c",
        f"user.name={cfg.commit_author_name}",
        "-c",
        f"user.email={cfg.commit_author_email}",
    )


def add_worktree(mirror: Path, path: Path, branch: str, base_ref: str) -> None:
    """Check out `branch` into a new worktree at `path`.

    When `branch` does not yet exist it is created at `base_ref`; when it does,
    it is checked out as-is and `base_ref` is ignored (the branch tip already
    carries whatever history matters).
    """
    mirror = Path(mirror)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if branch_exists(mirror, branch):
        git("worktree", "add", str(path), branch, cwd=mirror)
    else:
        git("worktree", "add", str(path), "-b", branch, base_ref, cwd=mirror)


def commit_all(worktree: Path, message: str, cfg: Config) -> str | None:
    """Stage everything (including untracked) and commit.

    Returns the new commit SHA, or `None` when the tree is clean and there is
    nothing to record.
    """
    worktree = Path(worktree)
    git("add", "-A", cwd=worktree)

    status = git("status", "--porcelain", cwd=worktree).stdout
    if not status.strip():
        return None

    git(*_identity(cfg), "commit", "-q", "-m", message, cwd=worktree)
    return git("rev-parse", "HEAD", cwd=worktree).stdout.strip()


def remove_worktree(mirror: Path, path: Path, force: bool = True) -> None:
    """Drop the worktree at `path`; the branch it was on is left untouched."""
    mirror = Path(mirror)
    path = Path(path)

    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    cp = git(*args, cwd=mirror, check=False)

    if cp.returncode != 0:
        # Already gone from disk, or git refuses to touch it: clear the
        # registration by hand so the mirror does not accumulate stale entries.
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        prune_worktrees(mirror)


def prune_worktrees(mirror: Path) -> None:
    """Clear registrations for worktrees whose directory no longer exists."""
    git("worktree", "prune", cwd=Path(mirror))


def write_json(worktree: Path, rel_path: str, data: dict) -> Path:
    """Write `data` as indented JSON at `worktree/rel_path`, creating parents."""
    target = Path(worktree) / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    return target
