"""Forge port — opening pull requests.

Landing proposes a change through it. The harness never touches the target
branch; it only opens a PR. The merge strategy is a human's call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from harness.models import Task


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    branch: str
    title: str
    repo: str


class PullRequestState(str, Enum):
    """The tri-state result of asking a forge what happened to a PR."""

    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"  # closed, not merged


class Forge(ABC):
    @abstractmethod
    def open_pull_request(
        self, task: Task, *, branch: str, title: str, body: str
    ) -> PullRequest:
        """Open a PR for the branch. Idempotent — if a PR for the branch already
        exists, return it instead of creating another."""

    @abstractmethod
    def base_branch(self, task: Task) -> str:
        """The branch a PR for this task will target (the repo's default branch).

        Landing merges this into the task branch before opening the PR, so the
        merge base is always exactly the branch the PR is opened against — a PR
        born up-to-date with its base. It is the same value `open_pull_request`
        targets, exposed so the merge can happen first; a driver that cannot
        determine it (GithubForge with no token) raises, exactly as
        `open_pull_request` would."""

    @abstractmethod
    def pull_request_state(self, task: Task) -> PullRequestState:
        """Current state of the PR referenced by task.data["pr"].

        Raises whatever the driver raises on failure (GithubForge: ForgeError) —
        PrWatcher treats any exception here as a transient failure to isolate,
        same as SourcePoller does around TaskSource.poll()."""
