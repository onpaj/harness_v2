"""`JiraIssuesCheck`: the inbound Jira label scan expressed as a `Check`.

The Jira twin of `GithubIssuesCheck` (`github_issues_check.py`) — runs a JQL
query (built from `project`+`label` when no explicit `jql` is given), claims
each matching issue by swapping the select label for the claimed one, and
returns one `Observation` per issue carrying `data.source` provenance
(`kind: "jira"`) so downstream code recognises the task's origin.

The `project`+`label` convenience form appends `AND statusCategory != Done`
so a resolved/closed issue still carrying the select label isn't
re-ingested on every tick — the Jira-side equivalent of GitHub's
`list_issues(..., state="open")` filter. An explicit `jql` override is
trusted as given and gets no implicit status clause.

Unlike a GitHub issue, a Jira issue carries no intrinsic repo axis, so every
observation this check emits is stamped with the single `repository`
configured at construction — the same mechanism `--heal-repo` uses to give a
repository-less heal task a worktree (invariant #25).

Registered into the process build as the `jira-issues` check by closing a
`JiraClient` into a factory in `cli.py`; imports only sibling drivers/ports —
never `cli` — so `test_architecture.py` stays green.
"""

from __future__ import annotations

from harness.drivers.jira_client import JiraClient
from harness.ports.triggers import Check, Observation


class JiraIssuesCheck(Check):
    def __init__(
        self,
        *,
        client: JiraClient,
        repository: str,
        label: str = "harness-todo",
        claimed_label: str = "harness-queued",
        jql: str | None = None,
        project: str | None = None,
    ) -> None:
        if jql is None and project is None:
            raise ValueError("jira-issues requires 'jql' or 'project'")
        self._client = client
        self._repository = repository
        self._label = label
        self._claimed_label = claimed_label
        self._jql = (
            jql
            or f'project = {project} AND labels = "{label}" AND statusCategory != Done'
        )
        # In-process ledger of already-claimed issue keys — the label swap
        # gives at-most-once across restarts, but `search_issues` reads with
        # the same read-after-write lag GitHub's `list_issues` has, so a fast
        # re-evaluate can still see the issue under the select label. This
        # cuts that off within the process.
        self._claimed: set[str] = set()

    def evaluate(self) -> list[Observation]:
        observations: list[Observation] = []
        for issue in self._client.search_issues(self._jql):
            if issue.key in self._claimed:
                continue
            self._claimed.add(issue.key)
            # Claim: swap the label before the task heads to the inbox.
            self._client.remove_label(issue.key, self._label)
            self._client.add_label(issue.key, self._claimed_label)
            observations.append(
                Observation(
                    state_key=f"jira:{issue.key}",
                    repository=self._repository,
                    data={
                        "title": issue.summary,
                        "body": issue.description,
                        "source": {
                            "kind": "jira",
                            "site": self._client.site,
                            "key": issue.key,
                            "url": issue.url,
                            "project": issue.project,
                        },
                    },
                )
            )
        return observations
