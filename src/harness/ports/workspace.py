"""Workspace port — the worktree where phases modify code.

`attach` connects the task to the worktree named in the task
(`repository`/`worktree`). The handle can write a file and commit. Committing is
the behavior driver's job, never the consumer's or the LLM's.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from harness.models import Task


class WorkspaceHandle(ABC):
    """A task's attached worktree."""

    @property
    @abstractmethod
    def path(self) -> Path:
        """The working directory."""

    @property
    @abstractmethod
    def branch(self) -> str:
        """The task branch the commits sit on."""

    @abstractmethod
    def write(self, relpath: str, content: str) -> None:
        """Write a file relative to the worktree.

        Landing also uses this to lay artifacts down — that's why it's a handle
        method rather than a direct write through `path`: the memory driver
        records it, so landing can be tested without a disk.
        """

    @abstractmethod
    def commit(self, message: str) -> str | None:
        """Stage everything and commit. Return the sha, or None if there is nothing to commit."""

    @abstractmethod
    def push(self) -> None:
        """Publish the task branch to `origin`.

        Landing calls this before proposing a PR — a forge cannot open one for
        a ref the remote has never seen. Idempotent: pushing an already-current
        branch is a no-op.
        """

    @abstractmethod
    def merge(self, base: str) -> bool:
        """Merge `origin/<base>` into the current branch, without committing.

        Returns True when the merge left conflict markers in the working tree
        (dirty — nothing was staged for a clean commit); False when the merge
        applied cleanly (already up to date, fast-forward, or an automatic
        merge) — the result is staged and ready for `commit()`.
        """

    @abstractmethod
    def abort_merge(self) -> None:
        """Abandon an in-progress merge, restoring the pre-merge working tree.

        Called only after `merge()` returned True (conflict markers present):
        landing has no agent to resolve them, so it drops the merge and opens
        the PR on the un-merged branch instead — the resolver workflow
        reconciles the dirty PR downstream. Never called when no merge is in
        progress.
        """


class Workspace(ABC):
    @abstractmethod
    def attach(self, task: Task) -> WorkspaceHandle:
        """Attach the task to its worktree. If none exists, create it on the task branch."""
