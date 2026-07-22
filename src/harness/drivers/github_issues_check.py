"""`GithubIssuesCheck`: the inbound `harness:todo` scan expressed as a `Check`.

The third distinct GitHub-issue concern in the codebase, kept in its own module:
`github_issues.py` is the self-heal `GithubIssueTracker`, `github_issue_checker.py`
is the `is_open` reconciler. This one is the *ingestion action* — it lists issues
by label across every repo in the registry, claims each by swapping the label
(the at-most-once side effect, exactly as `GithubTaskSource.poll`), and returns
one `Observation` per issue carrying `data.source` provenance so downstream
reconcilers/reflectors recognise the task.

It is registered into the process build as the `github-issues` check by closing a
`GithubClient` and the repo registry into a factory in `cli.py`; `BUILTIN_CHECKS`
stays client-free. Imports only sibling drivers and the registry port — never
`cli` — so `test_architecture.py` stays green.
"""

from __future__ import annotations

from harness.drivers.git_remote import github_slug
from harness.drivers.github_client import GithubClient
from harness.ports.repos import RepositoryRegistry
from harness.ports.triggers import Check, Observation


class GithubIssuesCheck(Check):
    def __init__(
        self,
        *,
        client: GithubClient,
        registry: RepositoryRegistry,
        slug_of=None,
        label: str = "harness:todo",
        claimed_label: str = "harness:queued",
    ) -> None:
        self._client = client
        self._registry = registry
        # Resolve the default at construction time (reads the module attribute
        # now) so tests can monkeypatch `github_slug`; an explicit slug_of wins.
        self._slug_of = slug_of or github_slug
        self._label = label
        self._claimed_label = claimed_label
        # In-process ledger of already-claimed issue numbers — the label
        # swap gives at-most-once across restarts, but `list_issues` reads with
        # read-after-write lag, so a fast re-evaluate can still see the issue
        # under the select label. This cuts that off within the process.
        self._claimed: set[tuple[str, int]] = set()

    def evaluate(self) -> list[Observation]:
        observations: list[Observation] = []
        for name in self._registry.names():
            slug = self._slug_of(self._registry.resolve(name))
            if slug is None:
                continue  # not a GitHub repo — nothing to scan
            for issue in self._client.list_issues(slug, label=self._label):
                key = (slug, issue.number)
                if key in self._claimed:
                    continue
                self._claimed.add(key)
                # Claim: swap the label before the task heads to the inbox.
                self._client.remove_label(slug, issue.number, self._label)
                self._client.add_label(slug, issue.number, self._claimed_label)
                observations.append(
                    Observation(
                        state_key=f"{slug}:{issue.number}",
                        repository=name,
                        data={
                            "title": issue.title,
                            "body": issue.body,
                            "source": {
                                "kind": "github",
                                "repo": slug,
                                "issue": issue.number,
                                "url": issue.url,
                            },
                        },
                    )
                )
        return observations
