"""GithubIssueTracker — opens an issue on GitHub, idempotently.

Reuses `GithubClient` (the same low-level client the source and forge drivers
use). Idempotency is by an embedded marker: a per-issue key written into the
body as an HTML comment. Before creating, it searches the open issues carrying
`scope_label` for that marker and returns the existing one if found — the twin
of `GithubForge` matching an existing PR on `head=owner:branch`.
"""

from __future__ import annotations

import urllib.error

from harness.drivers.github_client import GithubClient
from harness.ports.issues import IssueError, IssueRef, IssueTracker


def marker_comment(marker: str) -> str:
    """The hidden idempotency marker embedded in an opened issue's body."""
    return f"<!-- harness-issue:{marker} -->"


class GithubIssueTracker(IssueTracker):
    def __init__(self, client: GithubClient) -> None:
        self._client = client

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
        try:
            existing = self._client.search_issue_by_marker(
                repo, marker_comment(marker), label=scope_label
            )
            if existing is not None:
                return IssueRef(number=existing.number, url=existing.url)

            # Always carry the scope label (the search scopes to it) plus the
            # hidden marker, so a re-run finds this issue instead of opening a second.
            all_labels = tuple(dict.fromkeys((*labels, scope_label)))
            issue = self._client.create_issue(
                repo,
                title=title,
                body=f"{body}\n\n{marker_comment(marker)}\n",
                labels=all_labels,
            )
            return IssueRef(number=issue.number, url=issue.url)
        except urllib.error.HTTPError as error:
            raise IssueError(f"opening the issue on {repo} failed: {error}") from error
        except urllib.error.URLError as error:
            raise IssueError(f"opening the issue on {repo} failed: {error}") from error
