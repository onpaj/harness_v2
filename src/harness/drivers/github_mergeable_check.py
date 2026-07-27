"""`GithubMergeableCheck`: merge-candidate detection expressed as a `Check`.

The third sibling of `github_issues_check.py` (issue ingestion) and
`github_conflicts_check.py` (conflict detection), and the exact complement of
the latter. Both scan the same harness-authored open PRs and partition them by
`mergeable_state` with no overlap:

- `behind`  → the conflicts check updates the branch server-side (no task)
- `dirty`   → the conflicts check fires the `resolver` workflow
- `clean`   → **this check** fires the `automerge` workflow

`clean` is doing a lot of work here, and deliberately so: it is GitHub's own
verdict that the PR is mergeable, every *required* status check is green, and
every *required* review is present. So the harness never re-implements branch
protection — it defers to it, and an operator tightens the automerge gate by
tightening the repo's protection rules, where it belongs. Everything else
(`blocked`, `unstable`, `unknown`) is left alone rather than guessed at.

Three further exclusions are this check's own, all opt-out-shaped so the
default is the conservative one:

- a **draft** PR is never a candidate;
- a PR carrying `skip_label` (default `harness:no-automerge`) is never a
  candidate — the operator's per-PR veto, which needs no config to use;
- only PRs whose head matches `head_prefix` are seen at all (default
  `harness/`), so the harness proposes to merge only its own work unless an
  operator explicitly widens it.

Registered into the process build as the `github-mergeable` check by closing a
`GithubClient` and the repo registry into a factory in `cli.py`;
`BUILTIN_CHECKS` stays client-free. Imports only sibling drivers and the
registry port — never `cli` — so `test_architecture.py` stays green.
"""

from __future__ import annotations

from harness.drivers.git_remote import github_slug
from harness.drivers.github_client import GithubClient
from harness.ports.repos import RepositoryRegistry
from harness.ports.triggers import Check, CheckSpec, Observation, ParamSpec

DEFAULT_SKIP_LABEL = "harness:no-automerge"
"""The per-PR veto. A human who wants one PR kept out of the automerger's
reach adds this label; nothing else about the process needs to change."""

SOURCE_KIND = "pull-request"
"""The `source.kind` this check stamps on every automerge-review task it
mints. Named so `failed_tasks_check.py`'s recursion guard
(`PR_BORN_SOURCE_KINDS`) can import it rather than carry a second,
independent copy of the literal — a rename here then either propagates there
or breaks an import immediately, instead of silently disarming the guard."""

SPEC = CheckSpec(
    name="github-mergeable",
    label="GitHub mergeable PRs",
    description=(
        "Detects harness PRs that GitHub reports as cleanly mergeable, so an "
        "agent can review them for automatic merging."
    ),
    params=(
        ParamSpec(
            key="head_prefix",
            label="Branch prefix",
            placeholder="harness/",
            hint="Only PRs whose head branch starts with this prefix are watched.",
        ),
        ParamSpec(
            key="skip_label",
            label="Opt-out label",
            placeholder=DEFAULT_SKIP_LABEL,
            hint="A PR carrying this label is never considered for automatic merging.",
        ),
    ),
)
"""The action definition for `github-mergeable`. `cli.py` bundles it with the
factory that closes over a `GithubClient` + the repo registry."""


class GithubMergeableCheck(Check):
    def __init__(
        self,
        *,
        client: GithubClient,
        registry: RepositoryRegistry,
        slug_of=None,
        head_prefix: str = "harness/",
        skip_label: str = DEFAULT_SKIP_LABEL,
    ) -> None:
        self._client = client
        self._registry = registry
        # Resolve the default at construction (reads the module attribute now)
        # so tests can monkeypatch `github_slug`; an explicit slug_of wins.
        self._slug_of = slug_of or github_slug
        self._head_prefix = head_prefix
        self._skip_label = skip_label
        # In-process ledger, keyed `slug:number:head_sha` — the same shape as
        # the conflicts check's. Keying on the head sha is what makes a
        # re-pushed PR a genuinely new candidate (it must be reviewed again,
        # the old review having judged different code) while an unchanged one
        # is not re-reviewed every tick.
        self._seen: set[str] = set()

    def evaluate(self) -> list[Observation]:
        observations: list[Observation] = []
        for name in self._registry.names():
            slug = self._slug_of(self._registry.resolve(name))
            if slug is None:
                continue  # not a GitHub repo — nothing to scan
            for pr in self._client.list_pull_requests(slug, head_prefix=self._head_prefix):
                if pr.mergeable_state != "clean":
                    continue  # behind/dirty are the conflicts check's; the rest we don't guess at
                if pr.draft:
                    continue
                if self._skip_label and self._skip_label in pr.labels:
                    continue
                key = f"{slug}:{pr.number}:{pr.head_sha}"
                if key in self._seen:
                    continue
                self._seen.add(key)
                observations.append(
                    Observation(
                        state_key=key,
                        repository=name,
                        data={
                            # The task works on the PR's own branch — invariant
                            # #28's override, the same path the resolver uses.
                            "branch": pr.head_branch,
                            "title": f"review PR #{pr.number} for automatic merge",
                            "source": {
                                "kind": SOURCE_KIND,
                                "repo": slug,
                                "pr": pr.number,
                                "url": pr.url,
                                "base": pr.base_branch,
                                # The safety pin: the exact head the reviewer
                                # will read, carried through to the merge call
                                # so nothing merges that no agent reviewed.
                                "head_sha": pr.head_sha,
                                "pr_title": pr.title,
                            },
                        },
                    )
                )
        return observations
