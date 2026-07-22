"""Forge for a filesystem run — writes PRs to a file.

Opened PRs are stored as a JSON list in `<root>/prs.json`. Idempotent by branch:
if a PR for the given branch already exists, it is returned instead of creating
another. A real forge (GitHub etc.) comes later; this is enough for e2e and
smoke.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.models import Task
from harness.ports.forge import Forge, PullRequest, PullRequestState


class FakeForge(Forge):
    """Records PRs into `<root>/prs.json`. Idempotent by branch."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._file = self._root / "prs.json"

    def open_pull_request(
        self, task: Task, *, branch: str, title: str, body: str
    ) -> PullRequest:
        records = self._load()
        for record in records:
            if record["branch"] == branch:
                return self._to_pr(record)
        number = len(records) + 1
        record = {
            "number": number,
            "url": f"file://{self._root}/prs.json#{number}",
            "branch": branch,
            "title": title,
            "body": body,
            "state": "open",
            "merged": False,
            "repo": f"local/{branch}",
        }
        records.append(record)
        self._store(records)
        return self._to_pr(record)

    def pull_request_state(self, task: Task) -> PullRequestState:
        pr = task.data.get("pr")
        if not isinstance(pr, dict):
            raise RuntimeError(f"task {task.id}: carries no PR reference to check")
        branch = pr.get("branch")
        for record in self._load():
            if record["branch"] == branch:
                # Missing state/merged means a prs.json written before this
                # feature shipped — treat that as "still open", not an error.
                state = record.get("state", "open")
                merged = record.get("merged", False)
                if state == "open":
                    return PullRequestState.OPEN
                return PullRequestState.MERGED if merged else PullRequestState.CLOSED
        raise RuntimeError(f"task {task.id}: no PR found for branch {branch!r}")

    def close_pull_request(self, branch: str, *, merged: bool) -> None:
        """Test/smoke helper: simulate GitHub resolving the PR for `branch`."""
        records = self._load()
        for record in records:
            if record["branch"] == branch:
                record["state"] = "closed"
                record["merged"] = merged
                break
        self._store(records)

    def _load(self) -> list[dict]:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []

    def _store(self, records: list[dict]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @staticmethod
    def _to_pr(record: dict) -> PullRequest:
        return PullRequest(
            number=record["number"],
            url=record["url"],
            branch=record["branch"],
            title=record["title"],
            repo=record.get("repo") or f"local/{record['branch']}",
        )
