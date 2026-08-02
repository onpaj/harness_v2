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

**The process is capable of working all open PRs, not only the harness's own —
but a fresh install is withheld to `harness/`.** This is the sharp edge: at its
widest the harness pushes commits onto branches humans have checked out. Four
things contain it — it never force-pushes, a PR whose head lives in a fork is
never touched at all (its head branch is somebody else's, so acting on it would
mean committing to a same-named branch of the base repo, usually `main`),
`harness:no-autofix` is a per-PR veto needing no config change, and a
three-attempt budget held in a `harness:autofix-<n>@<sha7>` label ends in
`harness:needs-human` rather than looping.

That containment is what makes the wide setting *acceptable*; it is not what
makes it a sane **default**. `harness init` therefore seeds
`processes/unblock-pr.json` with `head_prefix: "harness/"`, matching the
sibling `automerge.json`, so a fresh root plus any `GITHUB_TOKEN` starts by
touching only branches the harness itself authored. Widening to `""` is one
field and no code change — the operator's explicit act, which is exactly the
posture `automerge`'s `dry_run: true` takes with the knob *it* has. The
operator's own `~/harness-root` is migrated by hand and deliberately runs
`""`.

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

**A give-up is labelled by the behavior, because the budget structurally cannot
reach it.** The stamp above makes the counter advance only on a new head sha —
and the `stuck` path produces no new head by design ("push nothing"; the merge
is aborted or committed locally and never pushed). So on the one path where the
agent has *declared the PR unfixable*, the attempt is never spent, the budget
never reaches `harness:needs-human`, and the PR is left carrying
`harness:autofix-1@<sha>` and no signal to any human. Worse, the settled task
lands in `done/`, `RetentionReconciler` archives it after
`HARNESS_RETENTION_DAYS`, and `_seed_pollers` seeds `SourcePoller._seen` from
`inbox`/step queues/`done`/`failed` — **not** `archived/`. The next restart
after archival therefore re-minted the identical `slug:pr:sha` task and spent
another agent run on the same unchanged PR, every retention window,
indefinitely.

`UnblockPrBehavior` therefore applies the give-up label itself on any non-`done`
outcome. That closes both halves at once: a human is told, and the check's
existing give-up guard — the first thing `_triage` reads after the fork guard —
skips the PR on every later tick, so there is nothing left to re-mint. The label
*value* travels on the task (`data["give_up_label"]`, stamped by the check) so
an operator renaming it in `processes/unblock-pr.json` renames both halves at
once; a constant duplicated in the wiring would have the behavior write a label
the check does not read, which is the re-mint loop again with extra steps. The
capability is injected as a callable (`PrLabeller`), not a `GithubClient`,
because `behaviors/` may not import `drivers/` (invariant 1) — the shape
`OpenIssueBehavior` already uses for `slug_for`. It is best-effort: a failed
label call annotates the summary rather than failing a task whose agent did
real work.

Three alternatives were rejected. *Spending an attempt on give-up* tells no
human until the third one and costs two more agent runs on a PR already
declared unfixable. *Seeding `_seen` from `archived/` too* fixes the re-mint
generally, but it is wrong generally: archival means "retired from the board",
not "this state may never produce work again" — a `github-issues` task archived
because its issue was closed would then never be re-ingested if the issue were
reopened and re-labelled, silently, and the seeded set would grow without bound
for the lifetime of the root. *Binding a `label-issue`-shaped finisher on the
`unblock` step* reaches the outcome from data, but a finisher kind registered
only when `GITHUB_TOKEN` is set makes a seeded workflow binding it fail the
whole run (exit 2) on every tokenless root — invariant 41's deliberate
`UnknownFinisherKind` posture.

## Consequences

- `blocked` is claimed only when a failing check-run exists. A PR merely
  awaiting a required review is left alone; no agent can supply one.
- The check now makes network calls per red PR (check-runs, then one log per
  failing run). The per-`head_sha` dedup key holds this to once per push.
- A rename of `SOURCE_KIND` must propagate to `PR_BORN_SOURCE_KINDS` in
  `failed_tasks_check.py`, which imports it precisely so that a rename breaks
  loudly instead of silently disarming the healer's one-hop guard.
- Widening or reverting the blast radius is a one-line config change:
  `head_prefix`, seeded `harness/`. No code change either way.
- The give-up label is applied by the run that gave up, so it needs a
  `GITHUB_TOKEN` — the same condition that gates the check minting the task at
  all. Without one, neither half runs, and there is nothing to re-mint. With
  one, a *failed* label call leaves the old loop intact for that PR until the
  next attempt succeeds; it is written into the task's summary rather than
  swallowed.
- The fork guard casefolds both sides. `slug` comes verbatim from
  `remote.origin.url` and `head_repo` from GitHub's canonical `full_name`, so a
  clone made with different casing (`OnPaj/Harness_v2`) failed the guard for
  *every* PR — no observations, no warning, a permanently green board, and
  every other API call still working because GitHub repo paths are
  case-insensitive.
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
  `processes/unblock-pr.json`, so a fresh root runs this — over `harness/`
  branches only. An earlier revision of this ADR argued that, unlike
  `automerge`, no half-measure was available because an unblock attempt either
  pushes a fix or does not run. That was wrong: `head_prefix` *is* the
  half-measure, and this ADR's own revert path already documented narrowing it.
  The seeded value is now the narrow one, and the operator widens it
  deliberately.
