"""`GithubConflictsCheck`: conflicted-PR detection expressed as a `Check`.

The mirror of `github_issues_check.py` for the resolver: where that one is the
*issue ingestion* action, this is the *conflict detection* action. It lists
harness-authored open PRs across every repo in the registry and, per PR,
either auto-updates a stale (`behind`) one server-side — a side effect that
produces no task, exactly as `GithubIssuesCheck` swaps a label — or returns one
`Observation` per conflicted (`dirty`) one, carrying the `data.branch` and
`data.source.base` the `resolver` workflow's back half already reads
(`ResolveConflictBehavior`, `GitWorkspace.attach`).

Registered into the process build as the `github-conflicts` check by closing a
`GithubClient` and the repo registry into a factory in `cli.py`; `BUILTIN_CHECKS`
stays client-free. Imports only sibling drivers and the registry port — never
`cli` — so `test_architecture.py` stays green.
"""

from __future__ import annotations

from harness.drivers.git_remote import github_slug
from harness.drivers.github_client import GithubClient
from harness.ports.repos import RepositoryRegistry
from harness.ports.triggers import Check, CheckSpec, Observation, ParamSpec

SPEC = CheckSpec(
    name="github-conflicts",
    label="GitHub conflicts",
    description="Detects harness PRs with merge conflicts.",
    params=(
        ParamSpec(
            key="head_prefix",
            label="Branch prefix",
            placeholder="harness/",
            hint="Only PRs whose head branch starts with this prefix are watched.",
        ),
    ),
)
"""The action definition for `github-conflicts`. `cli.py` bundles it with the
factory that closes over a `GithubClient` + the repo registry."""


class GithubConflictsCheck(Check):
    def __init__(
        self,
        *,
        client: GithubClient,
        registry: RepositoryRegistry,
        slug_of=None,
        head_prefix: str = "harness/",
    ) -> None:
        self._client = client
        self._registry = registry
        # Resolve the default at construction (reads the module attribute now)
        # so tests can monkeypatch `github_slug`; an explicit slug_of wins.
        self._slug_of = slug_of or github_slug
        self._head_prefix = head_prefix
        # In-process ledger of already-emitted conflicts, keyed by
        # `slug:number:head_sha`: `list_pull_requests` can re-list a PR before
        # its resolve task lands, and a per-head key lets a genuinely new head
        # (the conflict moved) re-emit while a repeat at the same head does not.
        self._seen: set[str] = set()

    def evaluate(self) -> list[Observation]:
        observations: list[Observation] = []
        for name in self._registry.names():
            slug = self._slug_of(self._registry.resolve(name))
            if slug is None:
                continue  # not a GitHub repo — nothing to scan
            for pr in self._client.list_pull_requests(slug, head_prefix=self._head_prefix):
                if pr.mergeable_state == "behind":
                    # Auto-update a stale branch, minting no task. Per-PR
                    # isolation: one bad PR must not sink the rest of the tick.
                    try:
                        self._client.update_branch(slug, pr.number)
                    except Exception:  # noqa: BLE001 - isolate one misbehaving PR
                        pass
                    continue
                if pr.mergeable_state != "dirty":
                    continue  # clean/blocked/unstable/unknown → leave alone (v1 scope)
                key = f"{slug}:{pr.number}:{pr.head_sha}"
                if key in self._seen:
                    continue
                self._seen.add(key)
                observations.append(
                    Observation(
                        state_key=key,
                        repository=name,
                        data={
                            "branch": pr.head_branch,
                            "title": f"resolve merge conflict on PR #{pr.number}",
                            "source": {
                                "kind": "mergeability",
                                "repo": slug,
                                "pr": pr.number,
                                "url": pr.url,
                                "base": pr.base_branch,
                            },
                        },
                    )
                )
        return observations
