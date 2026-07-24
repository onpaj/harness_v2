"""Jira client behind an ABC. Fake for tests, stdlib http for the real run.

Mirrors `github_client.py`'s shape exactly — one ABC naming the minimal
surface `JiraIssuesCheck` needs, one in-memory fake, one stdlib-`urllib` real
implementation. No new production dependency (no `requests`, no `jira` SDK).

`HttpJiraClient` targets Jira **Cloud** only (`/rest/api/3`, Basic auth with
an email + API token) — Server/Data Center (PAT auth, `/rest/api/2`) is a
later variant behind the same ABC.

Unlike `HttpGithubClient`, no error is wrapped in a driver-local exception
type: rereading `github_client.py` shows it doesn't define one either — a
non-2xx response propagates as the raw `urllib.error.HTTPError` except where
a specific status is a legitimate outcome (a 404 on `remove_label` is
"already gone", swallowed exactly like `HttpGithubClient.remove_label`).
`HttpJiraClient` mirrors that precedent rather than inventing a new taxonomy.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class JiraIssue:
    key: str  # e.g. "PROJ-123" — a string, unlike GitHub's int `number`
    summary: str  # -> task title
    description: str  # -> task body; plain text extracted from ADF
    url: str
    labels: tuple[str, ...]
    project: str  # the project key, e.g. "PROJ"


class JiraClient(ABC):
    """The minimal Jira Cloud API the loader needs."""

    @property
    @abstractmethod
    def site(self) -> str:
        """The configured Jira site base URL, e.g. `https://acme.atlassian.net`.

        Exposed so a caller (`JiraIssuesCheck`) can stamp it into provenance
        without a second, independently-configured copy of the same value.
        """

    @abstractmethod
    def search_issues(self, jql: str) -> list[JiraIssue]:
        """Issues matching a JQL query. The loader's twin of `list_issues`."""

    @abstractmethod
    def add_label(self, key: str, label: str) -> None:
        """Add a label. Adding one already set is a no-op (idempotent)."""

    @abstractmethod
    def remove_label(self, key: str, label: str) -> None:
        """Remove a label. A missing one is a no-op (idempotent)."""


class FakeJiraClient(JiraClient):
    """Issues in a dict, keyed by `key`. For unit/e2e — no network.

    `search_issues` doesn't run a real JQL engine — it extracts the quoted
    label from a `labels = "..."` predicate (the only shape `JiraIssuesCheck`
    ever builds or is handed in tests) and filters issues carrying it; a JQL
    with no such predicate returns every issue. Good enough for exercising
    the check without implementing Jira's query language.
    """

    def __init__(self, issues: list[JiraIssue] | None = None, *, site: str = "https://fake.atlassian.net") -> None:
        self._issues: dict[str, JiraIssue] = {i.key: i for i in (issues or [])}
        self._site = site

    @property
    def site(self) -> str:
        return self._site

    def add_issue(self, issue: JiraIssue) -> None:
        self._issues[issue.key] = issue

    def search_issues(self, jql: str) -> list[JiraIssue]:
        label = self._label_predicate(jql)
        if label is None:
            return list(self._issues.values())
        return [i for i in self._issues.values() if label in i.labels]

    @staticmethod
    def _label_predicate(jql: str) -> str | None:
        marker = 'labels = "'
        start = jql.find(marker)
        if start == -1:
            return None
        start += len(marker)
        end = jql.find('"', start)
        if end == -1:
            return None
        return jql[start:end]

    def add_label(self, key: str, label: str) -> None:
        issue = self._issues[key]
        if label not in issue.labels:
            self._issues[key] = replace(issue, labels=issue.labels + (label,))

    def remove_label(self, key: str, label: str) -> None:
        issue = self._issues.get(key)
        if issue is None or label not in issue.labels:
            return
        self._issues[key] = replace(
            issue, labels=tuple(l for l in issue.labels if l != label)
        )


def _adf_to_text(node: object) -> str:
    """Plain-text approximation of an Atlassian Document Format node.

    Recursively concatenates every `text` leaf, joining block-level children
    with newlines. Never raises on an unexpected shape (`None`, a plain
    string, a malformed dict) — a present-but-non-string description degrades
    to an empty/partial string rather than breaking the loader.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text", ""))
    children = node.get("content")
    if not isinstance(children, list):
        return ""
    return "\n".join(filter(None, (_adf_to_text(child) for child in children)))


class HttpJiraClient(JiraClient):
    """Real client against Jira Cloud's `/rest/api/3`. `opener` is injectable
    so it can be tested without a network; the default is stdlib
    `urllib.request.build_opener()`."""

    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        opener: Any = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api = f"{self._base_url}/rest/api/3"
        self._email = email
        self._api_token = api_token
        self._opener = opener or urllib.request.build_opener()

    @property
    def site(self) -> str:
        return self._base_url

    def _headers(self) -> dict[str, str]:
        credentials = f"{self._email}:{self._api_token}".encode("utf-8")
        token = base64.b64encode(credentials).decode("ascii")
        return {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def _json_headers(self) -> dict[str, str]:
        return {**self._headers(), "Content-Type": "application/json"}

    def search_issues(self, jql: str) -> list[JiraIssue]:
        query = urllib.parse.urlencode(
            {"jql": jql, "fields": "summary,description,labels,project"}
        )
        url = f"{self._api}/search?{query}"
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        with self._opener.open(request) as response:
            raw = json.loads(response.read())

        issues: list[JiraIssue] = []
        for item in raw.get("issues", []):
            fields = item.get("fields") or {}
            project = fields.get("project") or {}
            issues.append(
                JiraIssue(
                    key=item["key"],
                    summary=fields.get("summary") or "",
                    description=_adf_to_text(fields.get("description")),
                    url=f"{self._base_url}/browse/{item['key']}",
                    labels=tuple(fields.get("labels") or ()),
                    project=project.get("key", ""),
                )
            )
        return issues

    def add_label(self, key: str, label: str) -> None:
        self._update_labels(key, {"add": label})

    def remove_label(self, key: str, label: str) -> None:
        try:
            self._update_labels(key, {"remove": label})
        except urllib.error.HTTPError as error:
            if error.code == 404:  # issue or label is already gone → nothing to do
                return
            raise

    def _update_labels(self, key: str, op: dict[str, str]) -> None:
        url = f"{self._api}/issue/{key}"
        payload = json.dumps({"update": {"labels": [op]}}).encode("utf-8")
        request = urllib.request.Request(
            url, data=payload, headers=self._json_headers(), method="PUT"
        )
        self._opener.open(request)
