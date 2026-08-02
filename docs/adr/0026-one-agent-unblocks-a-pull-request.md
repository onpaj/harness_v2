# ADR-0026: One agent unblocks a pull request, and the check decides why

Status: Accepted (2026-07-31)

## Context

`github-conflicts` handled two of GitHub's `mergeable_state` values —
`behind` (updated server-side, no task) and `dirty` (the resolver workflow).
`unstable` and `blocked` were left alone, so a pull request with red CI sat
untouched until a human noticed. `GithubClient` had no check-run API, so there
was nothing to act on even if the check had wanted to.

Every process was also gated to the `harness/` branch prefix, so none of this
reached a human-authored PR.

## Decision

**Triage is deterministic and lives in the check.** `GithubUnhealthyPrsCheck`
partitions every open PR by `mergeable_state` and, for the ambiguous states,
by whether a failing check-run actually exists on the head sha. It fetches the
failing runs' logs, tails them, and emits one observation carrying a complete
brief. No agent is spawned to work out what is wrong.

The rejected alternative was a triage *step* — an agent that reads the PR and
routes to a specialist. It buys flexibility a check cannot have (noticing a
flaky test worth re-running rather than fixing) at the cost of one agent run
per unhealthy PR per push, and it moves the interesting logic somewhere no
test can reach without a model in the loop.

**One agent fixes whatever the brief describes.** There is no resolve/fix-CI
split. A PR that is both conflicted and red is one problem and gets one run.

**The process is scoped to all open PRs, not the harness's own.** This is the
sharp edge: the harness pushes commits onto branches humans have checked out.
Three things contain it — it never force-pushes, `harness:no-autofix` is a
per-PR veto needing no config change, and a three-attempt budget held in a
`harness:autofix-<n>` label ends in `harness:needs-human` rather than looping.

The budget lives on the PR because harness's own dedup ledger keys on
`head_sha`, and every fix push mints a new one — a counter there would reset
exactly when it matters.

## Consequences

- `blocked` is claimed only when a failing check-run exists. A PR merely
  awaiting a required review is left alone; no agent can supply one.
- The check now makes network calls per red PR (check-runs, then one log per
  failing run). The per-`head_sha` dedup key holds this to once per push.
- A rename of `SOURCE_KIND` must propagate to `PR_BORN_SOURCE_KINDS` in
  `failed_tasks_check.py`, which imports it precisely so that a rename breaks
  loudly instead of silently disarming the healer's one-hop guard.
- Reverting the blast radius is a one-line config change: set `head_prefix`
  back to `harness/`. No code change.
