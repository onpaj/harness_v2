"""Merging leaf run branches into a repo's integration branch.

The integration branch is the only branch this module ever moves. `base_branch`
(typically `main`) is read to seed the integration branch the first time and is
never written to — the harness must never be able to mutate a human's trunk.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentharness.config import Config
from agentharness.models import RepoDef

from .lock import repo_lock
from .mirror import (
    GitError,
    branch_exists,
    commit_time,
    create_branch,
    delete_branch,
    git,
    is_ancestor,
    list_branches,
    resolve_ref,
)
from .worktree import add_worktree, remove_worktree


class MergeConflict(RuntimeError):
    """A leaf branch could not be merged cleanly into the integration branch."""

    def __init__(self, branch: str, files: list[str]) -> None:
        self.branch = branch
        self.files = files
        super().__init__(
            f"merge of {branch!r} conflicted in {len(files)} file(s): "
            + ", ".join(files)
        )


def _identity(cfg: Config) -> tuple[str, ...]:
    return (
        "-c",
        f"user.name={cfg.commit_author_name}",
        "-c",
        f"user.email={cfg.commit_author_email}",
    )


def _conflicted_files(worktree: Path) -> list[str]:
    cp = git(
        "diff", "--name-only", "--diff-filter=U", cwd=worktree, check=False
    )
    return sorted({line for line in cp.stdout.splitlines() if line.strip()})


def merge_leaves(
    mirror: Path, branches: list[str], repo: RepoDef, cfg: Config
) -> str:
    """Merge each of `branches` into `repo.integration_branch` with `--no-ff`.

    Creates the integration branch at `repo.base_branch` if it does not exist.
    Returns the resulting integration SHA. Raises `MergeConflict` on the first
    branch that does not merge cleanly, leaving the integration branch at the
    last successful merge.
    """
    mirror = Path(mirror)
    integration = repo.integration_branch

    with repo_lock(cfg.home, repo.repo_id):
        if not branch_exists(mirror, integration):
            create_branch(mirror, integration, resolve_ref(mirror, repo.base_branch))

        cfg.worktrees_dir.mkdir(parents=True, exist_ok=True)
        wt = cfg.worktrees_dir / f"merge-{repo.repo_id}-{uuid.uuid4().hex[:12]}"
        add_worktree(mirror, wt, integration, repo.base_branch)

        try:
            for branch in branches:
                cp = git(
                    *_identity(cfg),
                    "merge",
                    "--no-ff",
                    "-m",
                    f"harness: merge {branch} into {integration}",
                    branch,
                    cwd=wt,
                    check=False,
                )
                if cp.returncode != 0:
                    files = _conflicted_files(wt)
                    git("merge", "--abort", cwd=wt, check=False)
                    if not files:
                        raise GitError(
                            ["git", "merge", "--no-ff", branch],
                            cp.returncode,
                            cp.stderr or cp.stdout,
                        )
                    raise MergeConflict(branch, files)
        finally:
            remove_worktree(mirror, wt)

        return resolve_ref(mirror, integration)


def gc_run_branches(mirror: Path, older_than_days: int, cfg: Config) -> list[str]:
    """Delete merged, aged-out `run/*` branches.

    A branch is only removed when its tip is both older than the retention
    window and already an ancestor of the integration branch, so unmerged work
    is never lost.
    """
    mirror = Path(mirror)
    integration = cfg.default_integration_branch
    if not branch_exists(mirror, integration):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    tip = resolve_ref(mirror, integration)

    deleted: list[str] = []
    for branch in list_branches(mirror, "run/*"):
        if commit_time(mirror, branch) >= cutoff:
            continue
        if not is_ancestor(mirror, resolve_ref(mirror, branch), tip):
            continue
        delete_branch(mirror, branch)
        deleted.append(branch)
    return deleted
