"""`GithubUnhealthyPrsCheck`: pull-request triage expressed as a `Check`.

The successor to the retired `github_conflicts_check.py`, and the exact
complement of `github_mergeable_check.py`. Both scan the same open PRs and
partition them by `mergeable_state` with no overlap:

- `behind` → **this check** updates the branch server-side (no task)
- `dirty`, or anything with a failing check run → **this check** fires the
  `unblock-pr` workflow
- `clean` → the mergeable check fires the `automerge` workflow

Every yes/no decision lives here rather than in an agent: triage is a pure
function of the PR's state, so it costs no tokens, survives a restart, and is
testable without a model in the loop. By the time a task exists, *what is
wrong with this PR* is already in `data.problem`, and the agent's only job is
fixing it.

`blocked` is the ambiguous state — GitHub uses it both for "a required check
failed" and for "a required review is missing". Only the former is claimed,
which is why the check-run fetch, not `mergeable_state`, is what decides.

Registered into the process build as the `github-unhealthy-prs` check by
closing a `GithubClient` and the repo registry into a factory in `cli.py`;
`BUILTIN_CHECKS` stays client-free. Imports only sibling drivers and the
registry port — never `cli` — so `test_architecture.py` stays green.
"""

from __future__ import annotations

import sys
from typing import Any

from harness.drivers.git_remote import github_slug
from harness.drivers.github_client import (
    FAILING_CONCLUSIONS,
    GithubClient,
    PullRequestInfo,
)
from harness.ports.repos import RepositoryRegistry
from harness.ports.triggers import Check, CheckSpec, Observation, ParamSpec

SOURCE_KIND = "pull-request-health"
"""The `source.kind` this check stamps on every task it mints. Named so
`failed_tasks_check.py`'s recursion guard (`PR_BORN_SOURCE_KINDS`) can import
it rather than carry a second, independent copy of the literal — a rename here
then either propagates there or breaks an import immediately, instead of
silently disarming the guard."""

DEFAULT_SKIP_LABEL = "harness:no-autofix"
"""The per-PR veto. A human who wants one PR kept out of the agent's reach
adds this label; nothing else about the process needs to change."""

DEFAULT_GIVE_UP_LABEL = "harness:needs-human"
"""Stamped once the attempt budget is spent, and read on every later tick as
"stop touching this". Removing it by hand is how an operator re-arms a PR."""

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_LOG_TAIL_LINES = 200

ATTEMPT_LABEL_PREFIX = "harness:autofix-"
ATTEMPT_LABEL_SEPARATOR = "@"
ATTEMPT_LABEL_SHA_LENGTH = 7
"""The rolling attempt counter, stored on the PR itself as
`harness:autofix-<n>@<sha7>` (e.g. `harness:autofix-2@3035f7d`, comfortably
inside GitHub's 50-character label limit).

The count cannot live in harness's own ledger: that keys on `head_sha`, and
every fix push mints a new one, so a counter there would reset exactly when it
matters. On the PR it is free to read (the labels arrive with the detail call
the check already makes), visible to whoever is looking at the PR, and an
operator resets the budget by deleting the label.

The `@<sha7>` half is what makes the counter count *attempts* rather than
*triages*. `evaluate()` is gated only by the in-process `_seen` ledger, while
the cross-restart guarantee lives downstream in `SourcePoller._seen`: the
poller silently drops a duplicate observation, but only after this check has
already written the label. Without the stamp, every restart — and this machine
restarts the service twice an hour — re-triaged an unchanged head and burned an
attempt, so a PR could reach `harness:needs-human` having had zero completed
attempts. The rule is therefore: **a label already naming the current head is
not bumped**, because re-triaging the same head is exactly the free case; only
a genuinely new head sha (what a real fix push produces) spends an attempt.

A *legacy* label written before the stamp existed (`harness:autofix-2`, no
`@sha`) names no head, so it can never match the current one: its count is
honoured and the next triage bumps it — the pre-stamp behaviour, which is the
backwards-compatible reading."""

SPEC = CheckSpec(
    name="github-unhealthy-prs",
    label="GitHub unhealthy PRs",
    description=(
        "Detects open pull requests that are conflicted or have failing "
        "checks, so an agent can unblock them. Stale branches are updated "
        "server-side without spending an agent."
    ),
    params=(
        ParamSpec(
            key="head_prefix",
            label="Branch prefix",
            hint=(
                "Only PRs whose head branch starts with this are watched. "
                "Empty means every open PR."
            ),
        ),
        ParamSpec(
            key="skip_label",
            label="Opt-out label",
            placeholder=DEFAULT_SKIP_LABEL,
            hint="A PR carrying this label is never touched.",
        ),
        ParamSpec(
            key="give_up_label",
            label="Give-up label",
            placeholder=DEFAULT_GIVE_UP_LABEL,
            hint="Added once the attempt budget is spent; the PR is then left alone.",
        ),
        ParamSpec(
            key="max_attempts",
            label="Attempts before giving up",
            type="number",
            placeholder=str(DEFAULT_MAX_ATTEMPTS),
        ),
        ParamSpec(
            key="log_tail_lines",
            label="Log tail (lines)",
            type="number",
            placeholder=str(DEFAULT_LOG_TAIL_LINES),
            hint="How much of each failing check's log reaches the agent.",
        ),
    ),
)
"""The action definition for `github-unhealthy-prs`. `cli.py` bundles it with
the factory that closes over a `GithubClient` + the repo registry."""


class GithubUnhealthyPrsCheck(Check):
    def __init__(
        self,
        *,
        client: GithubClient,
        registry: RepositoryRegistry,
        slug_of=None,
        head_prefix: str = "",
        skip_label: str = DEFAULT_SKIP_LABEL,
        give_up_label: str = DEFAULT_GIVE_UP_LABEL,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        log_tail_lines: int = DEFAULT_LOG_TAIL_LINES,
    ) -> None:
        self._client = client
        self._registry = registry
        # Resolve the default at construction (reads the module attribute now)
        # so tests can monkeypatch `github_slug`; an explicit slug_of wins.
        self._slug_of = slug_of or github_slug
        self._head_prefix = head_prefix
        self._skip_label = skip_label
        self._give_up_label = give_up_label
        self._max_attempts = max_attempts
        self._log_tail_lines = log_tail_lines
        # In-process ledger keyed `slug:number:head_sha`. Keying on the head
        # sha is what makes a re-pushed PR a genuinely new candidate while an
        # unchanged one is not re-emitted every tick.
        self._seen: set[str] = set()

    def evaluate(self) -> list[Observation]:
        observations: list[Observation] = []
        for name in self._registry.names():
            slug = self._slug_of(self._registry.resolve(name))
            if slug is None:
                continue  # not a GitHub repo — nothing to scan
            for pull in self._client.list_pull_requests(
                slug, head_prefix=self._head_prefix or None
            ):
                try:
                    observation = self._triage(name, slug, pull)
                except Exception as error:  # noqa: BLE001 - isolate one PR
                    # Isolation must not be silence. A systemic fault — a
                    # revoked token, a rate limit, a 5xx — fails *every* PR
                    # here, and with `head_prefix` defaulting to `""` that is
                    # now every open PR in every registered repo. Swallowed
                    # bare, the process just goes quiet while the board stays
                    # green, which reads as "nothing to fix". One line per
                    # failure on stderr (the same channel and `warning:`
                    # spelling `cli.py` uses) is enough to make it visible in
                    # the service log; the loop still carries on, because one
                    # misbehaving PR must not sink the tick.
                    print(
                        f"warning: github-unhealthy-prs could not triage "
                        f"{slug}#{pull.number}: "
                        f"{type(error).__name__}: {error}",
                        file=sys.stderr,
                    )
                    continue
                if observation is not None:
                    observations.append(observation)
        return observations

    def _triage(
        self, repository: str, slug: str, pull: PullRequestInfo
    ) -> Observation | None:
        """One PR's whole decision, in the order of the spec's table. Returns
        an `Observation` only for row 9; every earlier row returns None, some
        after a side effect."""
        if pull.head_repo != slug:
            # A fork PR (or one whose fork has been deleted, reported as no
            # head repo at all). Its head branch lives in someone else's repo,
            # so `data.branch` would name a branch of *this* repo that merely
            # shares the name — commonly `main`, since a fork PR is usually
            # opened from the contributor's own default branch. The workspace
            # would then check out, commit to and push the base repo's `main`,
            # violating ADR-0009. Fail closed: unknown head repo is not ours.
            return None
        if self._skip_label and self._skip_label in pull.labels:
            return None
        if self._give_up_label and self._give_up_label in pull.labels:
            return None
        if pull.draft:
            return None
        if pull.mergeable_state == "behind":
            # Un-stale the branch server-side, minting no task and spending no
            # agent. Idempotent, so it is safe on every tick.
            self._client.update_branch(slug, pull.number)
            return None
        if pull.mergeable_state == "clean":
            return None  # the mergeable check's candidate, not ours

        conflicted = pull.mergeable_state == "dirty"
        failing = [
            run
            for run in self._client.list_check_runs(slug, pull.head_sha)
            if run.conclusion in FAILING_CONCLUSIONS
        ]
        if not conflicted and not failing:
            # `blocked` awaiting a required review, or `unknown` while GitHub
            # is still computing — nothing here an agent could fix.
            return None

        spent, stamped_head = self._attempt_of(pull)
        head = pull.head_sha[:ATTEMPT_LABEL_SHA_LENGTH]
        # A label already naming this head means the attempt it counts is the
        # one being re-triaged (a restart, or a retry after the unblock task
        # failed) — free, by design. Anything else is a genuinely new head, and
        # that is what an attempt is spent on.
        fresh_head = stamped_head != head
        attempt = spent + 1 if fresh_head else spent
        if attempt > self._max_attempts:
            # Budget spent. Label it once and never look at it again — the
            # give-up check at the top of this method is what makes that stick.
            self._client.add_label(slug, pull.number, self._give_up_label)
            return None

        key = f"{slug}:{pull.number}:{pull.head_sha}"
        if key in self._seen:
            return None
        self._seen.add(key)

        if fresh_head:
            self._bump_attempt_label(slug, pull, attempt, head)

        failing_checks = [
            {
                "name": run.name,
                "url": run.url,
                "log_tail": self._tail(self._client.check_run_log(slug, run.id)),
            }
            for run in failing
        ]
        problem: dict[str, Any] = {
            "conflicted": conflicted,
            "attempt": attempt,
            "failing_checks": failing_checks,
        }
        return Observation(
            state_key=key,
            repository=repository,
            data={
                "branch": pull.head_branch,
                "title": f"unblock PR #{pull.number}",
                "body": _render_brief(problem, self._max_attempts),
                "problem": problem,
                "source": {
                    "kind": SOURCE_KIND,
                    "repo": slug,
                    "pr": pull.number,
                    "url": pull.url,
                    "base": pull.base_branch,
                },
            },
        )

    def _tail(self, log: str) -> str | None:
        """The last `log_tail_lines` lines, or None when there is no log.

        Tailing happens here rather than in the agent because a multi-megabyte
        log must never reach a prompt."""
        if not log.strip():
            return None
        lines = log.rstrip("\n").split("\n")
        return "\n".join(lines[-self._log_tail_lines :])

    def _attempt_of(self, pull: PullRequestInfo) -> tuple[int, str | None]:
        """How many attempts this PR has already had and against which head,
        read off its labels: `(attempt, sha7)`, or `(0, None)` when nothing
        readable is there.

        The **highest** attempt number wins, not the first one encountered:
        `_bump_attempt_label` adds before it removes, so two counters can
        legitimately be present at once, and `labels` is an unordered tuple —
        taking the first match would pick between them arbitrarily and could
        hand the budget back.

        An unparseable label counts as nothing rather than raising — a human
        editing labels by hand must not be able to wedge the check. A legacy
        label with no `@sha` parses to a real attempt with an unknown head
        (`None`), which never equals the current head, so it bumps.
        """
        attempt = 0
        head: str | None = None
        for label in pull.labels:
            if not label.startswith(ATTEMPT_LABEL_PREFIX):
                continue
            suffix = label[len(ATTEMPT_LABEL_PREFIX) :]
            count, _, sha = suffix.partition(ATTEMPT_LABEL_SEPARATOR)
            if not count.isdigit() or int(count) < 1:
                continue
            if int(count) > attempt:
                attempt, head = int(count), sha or None
        return attempt, head

    def _bump_attempt_label(
        self, slug: str, pull: PullRequestInfo, attempt: int, head: str
    ) -> None:
        """Stamp the new counter on, then clear the old ones — in that order.

        Removing first would mean a failure between the two calls (swallowed by
        `evaluate()`'s per-PR `except Exception: continue`) left the PR with no
        counter at all, silently resetting the budget to zero. Add-first fails
        the other way: the transient state is two counters, which `_attempt_of`
        resolves by taking the maximum.
        """
        fresh = f"{ATTEMPT_LABEL_PREFIX}{attempt}{ATTEMPT_LABEL_SEPARATOR}{head}"
        self._client.add_label(slug, pull.number, fresh)
        for label in pull.labels:
            if label.startswith(ATTEMPT_LABEL_PREFIX) and label != fresh:
                self._client.remove_label(slug, pull.number, label)


def _render_brief(problem: dict[str, Any], max_attempts: int) -> str:
    """The markdown `compose_prompt` puts in front of the agent as `data.body`.

    `data.problem` stays the structured record; this is only its rendering, so
    nothing in the prompt machinery has to learn to read a new shape."""
    lines = [
        f"This is attempt {problem['attempt']} of {max_attempts} at unblocking "
        "this pull request.",
        "",
    ]
    if problem["conflicted"]:
        lines += [
            "**The branch conflicts with its base.** The base has already been "
            "merged into your working directory, so the conflict markers are "
            "in the files in front of you.",
            "",
        ]
    if problem["failing_checks"]:
        lines.append("**Failing checks:**")
        lines.append("")
        for check in problem["failing_checks"]:
            lines.append(f"### {check['name']}")
            lines.append(f"<{check['url']}>")
            lines.append("")
            if check["log_tail"] is None:
                lines.append(
                    "_No log could be fetched for this check — work from its "
                    "name and the diff, and say so in your artifact._"
                )
            else:
                lines.append("```")
                lines.append(check["log_tail"])
                lines.append("```")
            lines.append("")
    return "\n".join(lines).strip()
