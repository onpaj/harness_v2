# ADR-0017: Landing syncs the base branch before proposing

Status: Accepted

## Context

A task's worktree branch (`harness/<task.id>`) is created from the repo's HEAD
when the task *starts* (`GitWorkspace.attach`) and is never re-synced with the
base branch while the task travels plan → design → … → land. On a repo where
`main` moves during that window — the normal case, not the exception — the PR
that landing opens is born *behind* its base. If the divergence is clean the PR
is merely stale (a human or auto-update must catch it up); if it overlaps the
task's own changes the PR is born **conflicted**, and only the out-of-band
resolver sweep (`GithubConflictsCheck` → resolver workflow, ADR-0014/0015-era
machinery) eventually notices and reconciles it.

The whole cost is paid *after* the PR exists, by a separate loop, on a branch
that already forked days ago. Nothing folds the base back in while the task is
still in its own pipeline, holding its own worktree, one step from `end`.

## Decision

Landing merges the PR's base branch into the task branch **before** it pushes
and proposes — the last thing it does with the worktree it already holds.

- The base is `Forge.base_branch(task)` (new port method): the exact branch the
  forge opens the PR against (GithubForge → the repo's default branch, cached
  per slug; the fakes → a configured name, default `"main"`). Sourcing the
  merge base from the forge — not a hardcoded `"main"`, not a git guess —
  guarantees the branch landing merges is the branch the PR targets, so "born
  up-to-date" is true by construction.
- The merge reuses the existing `WorkspaceHandle.merge(base)` (ADR: invariant
  #29 — always a merge, never a rebase). A **clean** merge is committed onto the
  task branch (`[land] merge <base>`), so the branch — and the PR — contains
  base's tip. An **"already up to date"** merge is a no-op commit (nothing
  staged), so a task that forked from an unchanged base is unaffected.
- A **conflict** landing cannot auto-resolve (it has no agent) is *abandoned* —
  `WorkspaceHandle.abort_merge()` (new) restores the pre-merge tree — and the
  PR is opened on the un-merged branch **anyway**, with the conflict flagged in
  the `BehaviorResult.summary`. The existing resolver workflow then reconciles
  the dirty PR exactly as it does for a conflict that appears after a PR is
  open. Landing never fails on a base conflict: the feature is a pure
  improvement (clean merges born mergeable) with the conflict path left at
  status quo, never a regression that parks work in `failed/`.

## Consequences

- The PR base branch must exist on `origin` for landing to fetch it — always
  true on a real forge (you cannot open a PR against a branch that isn't there).
  The e2e/smoke fixtures, which previously created a bare `origin` only to
  *push* to, now also publish their initial commit as `origin/main`, matching
  reality.
- `Forge` gains one abstract method (`base_branch`) and `WorkspaceHandle` one
  (`abort_merge`); both are implemented across every driver and test double.
  The merge base being the forge's concern keeps landing from ever guessing.
- `behavior_for` is untouched: landing is still selected as the `open-pr`
  finisher (ADR-0016). This ADR changes only what that one behavior does with
  the worktree before it proposes.
- No new port, loop, or wiring: the sync lives entirely inside
  `LandingBehavior.run`, between the artifact commit and the push.
