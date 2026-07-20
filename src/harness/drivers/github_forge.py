"""Forge against real GitHub — landing's outward half.

The slug is resolved **per task** from its worktree's git origin, the same way
`GithubTaskSource` resolves it per repo: the forge is constructed once but
serves every repo in `repos.json`, and `repos.json` holds no GitHub slug.

Every failure raises `ForgeError`. The consumer turns that into `failed/`, so a
task that reports "opened PR" always means a pull request that exists.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from harness.drivers.git_remote import github_slug
from harness.drivers.github_client import GithubClient
from harness.models import Task
from harness.ports.forge import Forge, PullRequest


class ForgeError(RuntimeError):
    """The forge could not open a pull request."""


class GithubForge(Forge):
    """Opens PRs on github.com. `client=None` means no `GITHUB_TOKEN`."""

    def __init__(
        self,
        client: GithubClient | None,
        *,
        slug_of: Callable[[Path], str | None] = github_slug,
    ) -> None:
        self._client = client
        self._slug_of = slug_of
        self._base: dict[str, str] = {}

    def open_pull_request(
        self, task: Task, *, branch: str, title: str, body: str
    ) -> PullRequest:
        client = self._client
        if client is None:
            raise ForgeError(
                "GITHUB_TOKEN is not set — cannot open a pull request. "
                "Export it, or run with --forge fake."
            )
        if not task.worktree:
            raise ForgeError(
                f"task {task.id} has no worktree — cannot resolve its GitHub repository"
            )
        slug = self._slug_of(Path(task.worktree))
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
                f"GitHub refused to open a pull request for {slug}: {error}"
            ) from error

        return PullRequest(
            number=created.number, url=created.url, branch=branch, title=title
        )

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
