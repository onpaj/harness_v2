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
Four things contain it — it never force-pushes, a PR whose head lives in a fork
is never touched at all (its head branch is somebody else's, so acting on it
would mean committing to a same-named branch of the base repo, usually `main`),
`harness:no-autofix` is a per-PR veto needing no config change, and a
three-attempt budget held in a `harness:autofix-<n>@<sha7>` label ends in
`harness:needs-human` rather than looping.

The budget lives on the PR because harness's own dedup ledger keys on
`head_sha`, and every fix push mints a new one — a counter there would reset
exactly when it matters.

**The label carries the head sha it was written against, and that is what makes
it count attempts rather than triages.** The cross-restart guarantee lives in
`SourcePoller._seen`, which drops the duplicate observation only *after* the
check has already written the label — so without the stamp, every restart of
the service (twice an hour on this machine) re-triaged an unchanged head and
spent an attempt, and a PR could reach `harness:needs-human` having had zero
completed attempts. A label already naming the current head is therefore never
bumped and writes no label at all; only a new head sha, which is what a real
fix push produces, spends an attempt. A legacy label with no `@sha` names no
head, so its count is honoured and the next triage bumps it — the pre-stamp
behaviour, kept deliberately so an upgrade neither loses nor regains budget.

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
- **On the success path the `unblock` agent's write-up is not persisted
  anywhere.** The commit excludes `.artifacts` so the write-up cannot ride into
  a human's pull request, which leaves it untracked in the worktree — and
  `land`'s reattach ends in an unconditional `clean -fd` (invariant 31) that
  deletes it. `WorktreeArtifactView` reads only the worktree, so from then on
  the board shows the task with no artifacts at all. Verified against a real
  git repository, not inferred. It *is* readable in the window between
  `unblock` finishing and `land` attaching, and it survives permanently on the
  `stuck` path, which goes straight to `end` and never reattaches — so the
  give-up case keeps its write-up and the success case loses it, the opposite
  of what an operator would guess. Anyone reasoning about "the write-up is in
  the task record" should read this bullet first: there is no record.
  Persisting it (copying the artifact out before the reattach, or committing it
  on a harness-owned branch) is a follow-up, deliberately not in this ADR.
- `harness init` seeds `workflows/unblock-pr.json`, `agents/unblock.json` and
  `processes/unblock-pr.json`, so a fresh root runs this — with
  `head_prefix: ""`, i.e. every open PR of every registered repository. That is
  this ADR's decision applied to the default install, not a separate one; the
  containment above is what makes it acceptable, and an operator who wants the
  old radius edits one field of the seeded process. Unlike `automerge`, there
  is no `dry_run` half-measure available: an unblock attempt either pushes a
  fix or does not run.
