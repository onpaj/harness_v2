"""IssueTracker port — opening a fresh advisory issue on a repo.

A third, distinct verb from the other two GitHub-touching ports:

- `Forge` (phase 2) opens **pull requests** — it proposes a change.
- `TaskSource.finish` (phase 4) **projects state** onto an *existing* issue by
  relabeling it.
- `IssueTracker.open_issue` **creates a fresh issue** — the healer's deliverable.

The healer diagnoses a failed task and, when it is a fixable harness bug, opens
an issue on the harness repo through this port. The worker (the `Healer` loop)
calls it, never the agent (invariant 9).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class IssueRef:
    """An opened issue as the tracker reports it."""

    number: int
    url: str


class IssueError(Exception):
    """Opening the issue failed (no token, a non-GitHub repo, an API error).

    Symmetric to `ForgeError`: the `Healer` loop catches it and settles the task
    to `healed/` with a `heal-failed` note — it never returns the task to
    `failed/`, so a failure to file cannot loop.
    """


class IssueTracker(ABC):
    @abstractmethod
    def open_issue(
        self,
        repo: str,
        *,
        title: str,
        body: str,
        labels: tuple[str, ...],
        marker: str,
        scope_label: str,
    ) -> IssueRef:
        """Open an issue on `repo`, carrying `scope_label` plus `labels`.

        **Idempotent per `(repo, scope_label, marker)`.** If an open issue on
        `repo` carrying `scope_label` already has the marker, return that one
        instead of creating a second — the twin of `GithubForge` matching an
        existing PR on `head=owner:branch`. `scope_label` is both a label every
        issue from a binding carries and the scope the marker search reads, so
        two bindings filing into the same repo never see each other's issues.
        Raises `IssueError` on failure.
        """
