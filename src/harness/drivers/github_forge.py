"""Forge against real GitHub — landing's outward half.

The slug is resolved **per task** from its worktree's git origin, the same way
`GithubTaskSource` resolves it per repo: the forge is constructed once but
serves every repo in `repos.json`, and `repos.json` holds no GitHub slug.

Every failure raises `ForgeError`. The consumer turns that into `failed/`, so a
task that reports "opened PR" always means a pull request that exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from harness.drivers.git_remote import github_slug
from harness.drivers.github_client import GithubClient
from harness.models import Task
from harness.ports.forge import Forge, PullRequest, PullRequestState
from harness.ports.repos import RepositoryRegistry


class ForgeError(RuntimeError):
    """The forge could not open a pull request."""


def _explain(error: Exception) -> str:
    """The API's own words, not just the status line.

    `urllib` raises `HTTPError` whose str() is only "HTTP Error 422:
    Unprocessable Entity" — useless. GitHub puts the reason in the response body
    ("No commits between main and ...", "A pull request already exists"), which
    is almost always the actual answer.
    """
    detail = ""
    read = getattr(error, "read", None)
    if callable(read):
        try:
            payload = json.loads(read().decode("utf-8"))
        except Exception:  # noqa: BLE001 - a non-JSON body is not worth failing over
            payload = None
        if isinstance(payload, dict):
            parts = [payload["message"]] if payload.get("message") else []
            for item in payload.get("errors") or []:
                if isinstance(item, dict) and item.get("message"):
                    parts.append(item["message"])
            detail = " — ".join(parts)
    return f"{error}: {detail}" if detail else str(error)


class GithubForge(Forge):
    """Opens PRs on github.com. `client=None` means no `GITHUB_TOKEN`."""

    def __init__(
        self,
        client: GithubClient | None,
        *,
        registry: RepositoryRegistry | None = None,
        slug_of: Callable[[Path], str | None] = github_slug,
    ) -> None:
        self._client = client
        self._registry = registry
        self._slug_of = slug_of
        self._base: dict[str, str] = {}

    def _repo_path(self, task: Task) -> Path | None:
        """Where the task's repository lives on this machine.

        The registry first: `task.repository` is a name and resolving it is
        exactly what the registry is for (invariant 15). `task.worktree` is only
        a fallback — `harness submit` leaves it unset, and a task that came that
        way used to fail landing with "has no worktree".
        """
        if self._registry is not None and task.repository:
            try:
                return Path(self._registry.resolve(task.repository))
            except Exception:  # noqa: BLE001 - unknown name falls through
                pass
        return Path(task.worktree) if task.worktree else None

    def open_pull_request(
        self, task: Task, *, branch: str, title: str, body: str
    ) -> PullRequest:
        client = self._client
        if client is None:
            raise ForgeError(
                "GITHUB_TOKEN is not set — cannot open a pull request. "
                "Export it, or run with --forge fake."
            )
        repo_path = self._repo_path(task)
        if repo_path is None:
            raise ForgeError(
                f"task {task.id}: cannot locate repository {task.repository!r} — "
                "not in repos.json and the task carries no worktree"
            )
        slug = self._slug_of(repo_path)
        if slug is None:
            raise ForgeError(
                f"{task.repository} has no GitHub origin — cannot open a pull request"
            )

        head = f"{slug.split('/')[0]}:{branch}"
        try:
            existing = client.find_pull_request(slug, head=head)
            if existing is not None:
                return PullRequest(
                    number=existing.number,
                    url=existing.url,
                    branch=branch,
                    title=title,
                )
            created = client.create_pull_request(
                slug,
                head=head,
                base=self._default_branch(client, slug),
                title=title,
                body=self._body(task, slug, body),
            )
        except ForgeError:
            raise
        except Exception as error:  # noqa: BLE001 - any API failure is a forge failure
            raise ForgeError(
                f"GitHub refused to open a pull request for {slug} "
                f"({head} -> {self._base.get(slug, '?')}): {_explain(error)}"
            ) from error

        return PullRequest(
            number=created.number, url=created.url, branch=branch, title=title
        )

    def pull_request_state(self, task: Task) -> PullRequestState:
        client = self._client
        if client is None:
            raise ForgeError(
                "GITHUB_TOKEN is not set — cannot check a pull request. "
                "Export it, or run with --forge fake."
            )
        repo_path = self._repo_path(task)
        if repo_path is None:
            raise ForgeError(
                f"task {task.id}: cannot locate repository {task.repository!r} — "
                "not in repos.json and the task carries no worktree"
            )
        slug = self._slug_of(repo_path)
        if slug is None:
            raise ForgeError(
                f"{task.repository} has no GitHub origin — cannot check a pull request"
            )
        pr = task.data.get("pr")
        if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
            raise ForgeError(f"task {task.id}: carries no PR reference to check")

        try:
            detail = client.get_pull_request(slug, number=pr["number"])
        except Exception as error:  # noqa: BLE001 - any API failure is a forge failure
            raise ForgeError(
                f"GitHub refused to fetch pull request {pr['number']} for {slug}: "
                f"{_explain(error)}"
            ) from error

        if detail.state == "open":
            return PullRequestState.OPEN
        return PullRequestState.MERGED if detail.merged else PullRequestState.CLOSED

    def _default_branch(self, client: GithubClient, slug: str) -> str:
        """The repo's default branch, fetched once per slug per process."""
        if slug not in self._base:
            self._base[slug] = client.default_branch(slug)
        return self._base[slug]

    @staticmethod
    def _body(task: Task, slug: str, body: str) -> str:
        """Append `Closes #n` when the task came from an issue on this repo."""
        source = task.data.get("source")
        if not isinstance(source, dict):
            return body
        if source.get("kind") != "github" or source.get("repo") != slug:
            return body
        number = source.get("issue")
        if not isinstance(number, int):
            return body
        return f"{body.rstrip()}\n\nCloses #{number}\n"
