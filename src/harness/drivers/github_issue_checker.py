"""`IssueChecker` against real GitHub — the issue reconciler's outward half.

Reads `repo`/`issue` straight off `task.data["source"]` at check time (no
per-repo construction, unlike `GithubForge`'s registry — one checker serves
every repo the token can reach). A `kind` other than `github`, or a task with no
source, yields `None`: not ours to judge. Any transient API failure propagates
unchanged, which is what makes `IssueChecker.is_open`'s "raise on transient
failure" contract true for free.
"""

from __future__ import annotations

from harness.drivers.github_client import GithubClient
from harness.models import Task
from harness.ports.issue_state import IssueChecker


class GithubIssueChecker(IssueChecker):
    kind = "github"

    def __init__(self, client: GithubClient) -> None:
        self._client = client

    def is_open(self, task: Task) -> bool | None:
        source = task.data.get("source")
        if not isinstance(source, dict) or source.get("kind") != self.kind:
            return None
        repo, number = source.get("repo"), source.get("issue")
        if not isinstance(repo, str) or not isinstance(number, int):
            return None
        # A deleted issue reads as `None` (missing) and a closed one as
        # "closed" — both are "not open", so both retire the task.
        return self._client.get_issue_state(repo, number) == "open"
