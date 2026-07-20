"""GitHub client behind an ABC. Fake for tests, stdlib http for the real run.

No new production dependency — `HttpGithubClient` runs on `urllib.request` +
`json`. If you're tempted to reach for `requests`/`httpx`, don't.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    url: str
    labels: tuple[str, ...]


class GithubClient(ABC):
    """The minimal GitHub API the connector needs."""

    @abstractmethod
    def list_issues(self, repo: str, *, label: str) -> list[Issue]:
        """Open issues with the given label (no PRs)."""

    @abstractmethod
    def add_label(self, repo: str, number: int, label: str) -> None:
        """Add a label. Adding one that is already set is a no-op."""

    @abstractmethod
    def remove_label(self, repo: str, number: int, label: str) -> None:
        """Remove a label. A missing one is a no-op (idempotent)."""


class FakeGithubClient(GithubClient):
    """Issues in a dict. For unit/e2e and smoke — no network."""

    def __init__(self, issues: list[Issue] | None = None) -> None:
        self._issues: dict[int, Issue] = {i.number: i for i in (issues or [])}

    def add_issue(self, issue: Issue) -> None:
        self._issues[issue.number] = issue

    def list_issues(self, repo: str, *, label: str) -> list[Issue]:
        return [i for i in self._issues.values() if label in i.labels]

    def add_label(self, repo: str, number: int, label: str) -> None:
        issue = self._issues[number]
        if label not in issue.labels:
            self._issues[number] = replace(issue, labels=issue.labels + (label,))

    def remove_label(self, repo: str, number: int, label: str) -> None:
        issue = self._issues.get(number)
        if issue is None or label not in issue.labels:
            return
        self._issues[number] = replace(
            issue, labels=tuple(l for l in issue.labels if l != label)
        )


class HttpGithubClient(GithubClient):
    """Real client against `api.github.com`. `opener` is injectable so it can be
    tested without a network; the default is stdlib `urllib.request.build_opener()`."""

    def __init__(
        self,
        token: str,
        *,
        api: str = "https://api.github.com",
        opener: Any = None,
    ) -> None:
        self._token = token
        self._api = api.rstrip("/")
        self._opener = opener or urllib.request.build_opener()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
        }

    def list_issues(self, repo: str, *, label: str) -> list[Issue]:
        query = urllib.parse.urlencode({"state": "open", "labels": label})
        url = f"{self._api}/repos/{repo}/issues?{query}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with self._opener.open(request) as response:
            raw = json.loads(response.read())

        issues: list[Issue] = []
        for item in raw:
            if "pull_request" in item:  # PRs are also "issues" — filter them out
                continue
            issues.append(
                Issue(
                    number=item["number"],
                    title=item.get("title", ""),
                    body=item.get("body") or "",
                    url=item.get("html_url", ""),
                    labels=tuple(l["name"] for l in item.get("labels", [])),
                )
            )
        return issues

    def add_label(self, repo: str, number: int, label: str) -> None:
        url = f"{self._api}/repos/{repo}/issues/{number}/labels"
        body = json.dumps({"labels": [label]}).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, headers=self._headers(), method="POST"
        )
        self._opener.open(request)

    def remove_label(self, repo: str, number: int, label: str) -> None:
        quoted = urllib.parse.quote(label, safe="")
        url = f"{self._api}/repos/{repo}/issues/{number}/labels/{quoted}"
        request = urllib.request.Request(url, headers=self._headers(), method="DELETE")
        try:
            self._opener.open(request)
        except urllib.error.HTTPError as error:
            if error.code == 404:  # label is already gone → nothing to do
                return
            raise
