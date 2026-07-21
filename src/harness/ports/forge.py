"""Forge port — opening pull requests.

Landing proposes a change through it. The harness never touches the target
branch; it only opens a PR. The merge strategy is a human's call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from harness.models import Task


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    branch: str
    title: str
    repo: str


class Forge(ABC):
    @abstractmethod
    def open_pull_request(
        self, task: Task, *, branch: str, title: str, body: str
    ) -> PullRequest:
        """Open a PR for the branch. Idempotent — if a PR for the branch already
        exists, return it instead of creating another."""
