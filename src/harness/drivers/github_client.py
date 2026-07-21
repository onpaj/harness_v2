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


@dataclass(frozen=True)
class PullRequestRef:
    """A pull request as the API returns it. `head` is the `owner:branch` form."""

    number: int
    url: str
    head: str


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

    @abstractmethod
    def default_branch(self, repo: str) -> str:
        """The repo's default branch — what a PR is opened against."""

    @abstractmethod
    def find_pull_request(self, repo: str, *, head: str) -> PullRequestRef | None:
        """The open PR for `head` (`owner:branch`), or None."""

    @abstractmethod
    def create_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        """Open a PR from `head` into `base`."""

    @abstractmethod
    def create_issue(
        self, repo: str, *, title: str, body: str, labels: tuple[str, ...]
    ) -> Issue:
        """Open a fresh issue on `repo`."""

    @abstractmethod
    def search_issue_by_marker(self, repo: str, marker: str) -> Issue | None:
        """The open self-heal issue whose body carries `marker`, or None.

        Used to keep issue creation idempotent without the Search API — it scans
        the `harness:self-heal`-labelled open issues and matches the marker in
        the body.
        """


SELF_HEAL_LABEL = "harness:self-heal"
"""Label every healer-opened issue carries — also the scope of the marker search."""


class FakeGithubClient(GithubClient):
    """Issues in a dict. For unit/e2e and smoke — no network."""

    def __init__(
        self, issues: list[Issue] | None = None, *, default_branch: str = "main"
    ) -> None:
        self._issues: dict[int, Issue] = {i.number: i for i in (issues or [])}
        self._default_branch = default_branch
        self.pulls: list[PullRequestRef] = []
        self.created: list[dict] = []

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

    def default_branch(self, repo: str) -> str:
        return self._default_branch

    def find_pull_request(self, repo: str, *, head: str) -> PullRequestRef | None:
        for pull in self.pulls:
            if pull.head == head:
                return pull
        return None

    def create_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        number = len(self.pulls) + 1
        pull = PullRequestRef(
            number=number,
            url=f"https://github.com/{repo}/pull/{number}",
            head=head,
        )
        self.pulls.append(pull)
        self.created.append(
            {"repo": repo, "head": head, "base": base, "title": title, "body": body}
        )
        return pull

    def create_issue(
        self, repo: str, *, title: str, body: str, labels: tuple[str, ...]
    ) -> Issue:
        number = (max(self._issues) if self._issues else 0) + 1
        issue = Issue(
            number=number,
            title=title,
            body=body,
            url=f"https://github.com/{repo}/issues/{number}",
            labels=tuple(labels),
        )
        self._issues[number] = issue
        return issue

    def search_issue_by_marker(self, repo: str, marker: str) -> Issue | None:
        for issue in self.list_issues(repo, label=SELF_HEAL_LABEL):
            if marker in issue.body:
                return issue
        return None


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

    def _json_headers(self) -> dict[str, str]:
        """Headers for a request that carries a JSON body (POST/PATCH)."""
        return {**self._headers(), "Content-Type": "application/json"}

    @staticmethod
    def _pr_head(item: dict, fallback: str) -> str:
        """The `owner:branch` label as the server reported it.

        Falls back to the caller's `head` argument when the response is missing
        the field or it isn't shaped as expected, so a partial response degrades
        instead of raising.
        """
        head = item.get("head")
        if isinstance(head, dict):
            label = head.get("label")
            if isinstance(label, str):
                return label
        return fallback

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
            url, data=body, headers=self._json_headers(), method="POST"
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

    def default_branch(self, repo: str) -> str:
        url = f"{self._api}/repos/{repo}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with self._opener.open(request) as response:
            return json.loads(response.read())["default_branch"]

    def find_pull_request(self, repo: str, *, head: str) -> PullRequestRef | None:
        query = urllib.parse.urlencode({"state": "open", "head": head})
        url = f"{self._api}/repos/{repo}/pulls?{query}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with self._opener.open(request) as response:
            raw = json.loads(response.read())
        for item in raw:
            return PullRequestRef(
                number=item["number"],
                url=item.get("html_url", ""),
                head=self._pr_head(item, head),
            )
        return None

    def create_pull_request(
        self, repo: str, *, head: str, base: str, title: str, body: str
    ) -> PullRequestRef:
        url = f"{self._api}/repos/{repo}/pulls"
        payload = json.dumps(
            {"head": head, "base": base, "title": title, "body": body}
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers=self._json_headers(),
            method="POST",
        )
        with self._opener.open(request) as response:
            item = json.loads(response.read())
        return PullRequestRef(
            number=item["number"],
            url=item.get("html_url", ""),
            head=self._pr_head(item, head),
        )

    def create_issue(
        self, repo: str, *, title: str, body: str, labels: tuple[str, ...]
    ) -> Issue:
        url = f"{self._api}/repos/{repo}/issues"
        payload = json.dumps(
            {"title": title, "body": body, "labels": list(labels)}
        ).encode("utf-8")
        request = urllib.request.Request(
            url, data=payload, headers=self._json_headers(), method="POST"
        )
        with self._opener.open(request) as response:
            item = json.loads(response.read())
        return Issue(
            number=item["number"],
            title=item.get("title", title),
            body=item.get("body") or body,
            url=item.get("html_url", ""),
            labels=tuple(l["name"] for l in item.get("labels", [])),
        )

    def search_issue_by_marker(self, repo: str, marker: str) -> Issue | None:
        for issue in self.list_issues(repo, label=SELF_HEAL_LABEL):
            if marker in issue.body:
                return issue
        return None
