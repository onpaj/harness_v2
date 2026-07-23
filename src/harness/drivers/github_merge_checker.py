"""`MergeChecker` against real GitHub — the reconciler's outward half.

Reads `repo`/`number` straight off `task.data["pr"]` at check time (no
per-repo construction, unlike `GithubForge`'s registry). Any API failure
propagates unchanged — that's what makes `MergeChecker.is_merged`'s "raise on
transient failure" contract true for free.
"""

from __future__ import annotations

from harness.drivers.github_client import GithubClient
from harness.models import Task
from harness.ports.merge import MergeChecker


class GithubMergeChecker(MergeChecker):
    def __init__(self, client: GithubClient) -> None:
        self._client = client

    def is_merged(self, task: Task) -> bool | None:
        pr = task.data.get("pr")
        if not isinstance(pr, dict):
            return None
        repo, number = pr.get("repo"), pr.get("number")
        if not isinstance(repo, str) or not isinstance(number, int):
            return None
        return self._client.get_pull_request(repo, number).merged
