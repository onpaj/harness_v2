"""Bare-mirror git primitives.

This is the only module in the harness allowed to invoke git as a subprocess.
Everything here funnels through `git()`, which is the single choke point for
environment hardening (no credential prompts) and error translation.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# Committer identity for harness-created commits that have no user context.
_IDENTITY = (
    "-c",
    "user.name=agentharness",
    "-c",
    "user.email=agentharness@localhost",
)

EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GitError(RuntimeError):
    """A git invocation exited non-zero."""

    def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"git command failed ({returncode}): {' '.join(argv)}\n{stderr.strip()}"
        )


def git(
    *args: str,
    cwd: Path | None = None,
    check: bool = True,
    env: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run git, capturing text output.

    `GIT_TERMINAL_PROMPT=0` is always set so a credential prompt fails fast
    instead of hanging a worker forever on a blocked tty read.
    """
    argv = ["git", *[str(a) for a in args]]
    child_env = dict(os.environ)
    if env:
        child_env.update({str(k): str(v) for k, v in env.items()})
    child_env["GIT_TERMINAL_PROMPT"] = "0"

    cp = subprocess.run(
        argv,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        env=child_env,
    )
    if check and cp.returncode != 0:
        raise GitError(argv, cp.returncode, cp.stderr)
    return cp


def clone_mirror(url: str, dest: Path) -> None:
    """Clone `url` as a bare mirror at `dest`."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    git("clone", "--mirror", str(url), str(dest))


def init_bare(dest: Path) -> None:
    """Initialise an empty bare repo at `dest`."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    git("init", "--bare", "-q", str(dest))


def empty_root_commit(bare: Path, branch: str = "main") -> str:
    """Create a parentless commit with an empty tree and point `branch` at it."""
    tree_sha = git(
        "hash-object", "-t", "tree", "-w", os.devnull, cwd=bare
    ).stdout.strip()
    sha = git(
        *_IDENTITY,
        "commit-tree",
        tree_sha,
        "-m",
        "root",
        cwd=bare,
    ).stdout.strip()
    git("update-ref", f"refs/heads/{branch}", sha, cwd=bare)
    return sha


def fetch(mirror: Path) -> None:
    """Update all mirrored refs from the origin remote."""
    git("fetch", "--prune", "origin", "+refs/heads/*:refs/heads/*", cwd=mirror)


def resolve_ref(mirror: Path, ref: str) -> str:
    """Resolve `ref` to a full commit SHA; raises `GitError` if unknown."""
    return git("rev-parse", "--verify", f"{ref}^{{commit}}", cwd=mirror).stdout.strip()


def branch_exists(mirror: Path, branch: str) -> bool:
    cp = git(
        "show-ref", "--verify", "--quiet", f"refs/heads/{branch}",
        cwd=mirror,
        check=False,
    )
    return cp.returncode == 0


def create_branch(mirror: Path, branch: str, at: str) -> None:
    git("update-ref", f"refs/heads/{branch}", at, cwd=mirror)


def delete_branch(mirror: Path, branch: str) -> None:
    git("update-ref", "-d", f"refs/heads/{branch}", cwd=mirror)


def list_branches(mirror: Path, pattern: str = "*") -> list[str]:
    """Branch names (without the `refs/heads/` prefix) matching `pattern`."""
    cp = git(
        "for-each-ref",
        "--format=%(refname:short)",
        f"refs/heads/{pattern}",
        cwd=mirror,
    )
    return [line for line in cp.stdout.splitlines() if line.strip()]


def commit_time(mirror: Path, ref: str) -> datetime:
    """Committer timestamp of `ref` as a timezone-aware UTC datetime."""
    raw = git("show", "-s", "--format=%ct", f"{ref}^{{commit}}", cwd=mirror).stdout
    return datetime.fromtimestamp(int(raw.strip()), tz=timezone.utc)


def is_ancestor(mirror: Path, a: str, b: str) -> bool:
    """True when commit `a` is an ancestor of commit `b`."""
    cp = git("merge-base", "--is-ancestor", a, b, cwd=mirror, check=False)
    if cp.returncode not in (0, 1):
        raise GitError(
            ["git", "merge-base", "--is-ancestor", a, b], cp.returncode, cp.stderr
        )
    return cp.returncode == 0
