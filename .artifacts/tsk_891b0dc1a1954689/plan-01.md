# Plan — fix `GitWorkspace.attach()` discarding the resolver's un-pushed merge on the `resolve → land` hand-off

## Summary

`GitWorkspace.attach()`'s branch-override reattach path (`drivers/git_workspace.py`) unconditionally
resets the worktree to `origin/<branch>` on every reattach. That's correct when the *only* thing that
could have changed is the remote (a resolver retry, or `GithubMergeabilityWatcher.update_branch`
advancing the branch server-side) — but the resolver workflow's own `resolve → land` hand-off reattaches
the *same* worktree within the *same task run*, after `resolve` has already produced a real, un-pushed,
local two-parent merge commit. The reset silently throws that commit away before `land` pushes, so the PR
head never advances, `open_pull_request` returns the pre-existing (still conflicting) PR, and `land`
reports `done`. The task looks successful; the PR stays `CONFLICTING` forever, because the mergeability
watcher's `dedup_key` is keyed on the PR's head SHA, which never moved — every later detection is
`duplicate_ignored`. This matches filed GitHub issue **#86** verbatim.

## Context

The resolver feature (`resolve → land → end`, `GithubMergeabilityWatcher`, `ResolveConflictBehavior`) is
already implemented on `origin/main` (this task's worktree branch is currently **65 commits behind**
`origin/main` — merging up is a mandatory first step of development, per this repo's established
convention of merge-only sync-before-edit). The reset-to-origin behavior itself was added deliberately in
an earlier fix (commit `06cfacb`/`cfc2707`, CLAUDE.md invariant 31) to solve a *different* problem: a
resolver retry whose shared local branch ref had gone stale because `update_branch` advances a PR
server-side with no local git trace. That fix over-generalized: it resets on **every** override reattach,
not just the ones where there is nothing valuable to lose locally. The `resolve → land` hand-off is the
case it broke — same task, same worktree, a genuine local commit sitting ahead of `origin`, not yet
pushed. Two live PRs (**#82**, **#85**, since separately recovered/merged) demonstrated this in
production.

There is no existing test that reproduces this: `tests/test_mergeability_e2e.py` drives the full
`resolve → land` flow but against `MemoryWorkspace`, whose fake `attach()` never resets or discards
anything — it can't catch a real-git reset bug. `tests/test_git_workspace.py` has real-git coverage of
the override reattach path, but only for the "origin advanced, local has nothing to lose" scenario
(`test_attach_reattach_with_override_reconciles_with_origin_after_server_side_advance`); nothing covers
"local has advanced past origin."

## Functional requirements

**FR-1 — Preserve an un-pushed local commit on override reattach.**
When `GitWorkspace.attach()` reattaches an existing worktree with `task.data["branch"]` set (the
`elif override:` path) and local `HEAD` is *ahead* of `origin/<branch>` (an ancestor relationship with
origin behind), it must **not** reset the worktree — the local commit(s) must survive the reattach
unchanged.
- AC1: drive `resolve` (real `git merge --no-commit --no-ff` + agent-resolved commit via
  `GitWorkspaceHandle.merge`/`commit`) then reattach for `land` on the same task/worktree, with `origin`
  unchanged since `resolve` ran — the merge commit's SHA is still `HEAD` after the `land` reattach.
- AC2: `land`'s subsequent `push()` advances `origin/<branch>` to that merge commit (not a no-op).

**FR-2 — Keep reconciling when local is behind (unchanged behavior).**
When local `HEAD` is an ancestor of `origin/<branch>` (equal or behind — a resolver retry with nothing
local to lose, or the existing server-side-advance scenario), reattach must still hard-reset to
`origin/<branch>` exactly as today.
- AC1: all four existing override-reattach tests in `tests/test_git_workspace.py` keep passing unmodified
  (`test_attach_with_branch_override_force_checks_out_already_checked_out_branch`,
  `..._creates_from_origin_when_no_local_copy`, `..._reconciles_stale_local_ref_with_origin`,
  `test_attach_reattach_with_override_reconciles_with_origin_after_server_side_advance`).
- AC2: `test_reattach_without_override_still_resets_to_local_head` and
  `test_attach_without_override_is_unchanged` keep passing — the non-override path is untouched.

**FR-3 — Fail loudly, never silently, on genuine divergence.**
When local `HEAD` and `origin/<branch>` have both advanced independently since the worktree was last
reconciled (neither is an ancestor of the other — local has an un-pushed commit *and* the remote moved
too, e.g. a racing `update_branch` mid-resolve), `attach()` must raise (`GitError`) rather than pick a
side silently. The consumer already routes any raised exception from a behavior to `failed/`
(`consumer.py`, invariant #3) with no code change needed there.
- AC1: a real-git test simulating both a local-ahead commit and an independent origin advance asserts
  `attach()` raises and neither ref is mutated.
- AC2: the task lands in `failed/`, not silently in `done/`.

**FR-4 — Regression test for the exact reported bug shape.**
Add a real-git test in `tests/test_git_workspace.py` (or a small new test module alongside it) that
reproduces the `resolve → land` hand-off end-to-end against a real bare remote: attach with override,
merge conflicting content from a "base" branch, resolve the conflict, commit (mirroring
`ResolveConflictBehavior`), reattach (mirroring `LandingBehavior.run`'s second `attach()` call), push, and
assert the remote's branch tip equals the resolved merge commit — i.e. it must fail against the current
code and pass after the fix (the report's own suggested TDD shape).

**FR-5 (optional, not blocking) — Make the in-memory fake capable of catching this class of bug.**
`MemoryWorkspace`/`MemoryWorkspaceHandle` never model reset-on-reattach at all, so
`test_mergeability_e2e.py` cannot regress-test this at the workflow level. Consider whether it's worth
teaching the fake to track a `pushed_head`/`local_head` distinction so a future e2e test could assert the
final pushed ref without going through real git. Left as an open question for design — not required to
close #86, since FR-4's real-git test already covers the reported defect.

## Non-functional requirements

- **Correctness over cleverness**: the fix must be expressible as a small, explainable ancestry check
  (`git merge-base --is-ancestor`), not a broader rewrite of `attach()`'s control flow. Every existing
  invariant (28, 30, 31) and passing test must remain intact.
- **No behavior change outside the `elif override:` reattach branch.** The non-override reattach
  (`else:` branch, local `HEAD` reset) and the initial-create branches (`if not worktree.exists(): ...`)
  are out of scope.
- **No new production dependency** — stay on subprocess `git`, matching the existing driver's stated
  constraint.
- **Auditability**: when `attach()` raises on divergence (FR-3), the `GitError` message must name the
  branch and both SHAs, so a failed task's history entry is actionable from the board UI alone.

## Data model

No changes to `Task`, `BehaviorResult`, or any port shape. This is entirely internal to
`GitWorkspace.attach()`'s reattach branch — the fix changes *when* a reset happens, not any public
interface. No new fields, no new events.

## Interfaces

- `GitWorkspace.attach(task: Task) -> GitWorkspaceHandle` — same signature; internal reattach-with-override
  logic gains an ancestry check before deciding whether to reset.
- No new port, no new CLI flag, no new HTTP route. `ResolveConflictBehavior` and `LandingBehavior` are
  unchanged callers — the fix is entirely inside the workspace driver they both call.

## Dependencies and scope

**In scope:**
- `src/harness/drivers/git_workspace.py` — the `elif override:` reattach branch in `GitWorkspace.attach()`.
- `tests/test_git_workspace.py` (or a sibling test file) — new regression coverage per FR-1/FR-3/FR-4.
- `CLAUDE.md` — invariant 31 currently states the reattach-with-override path *always* reconciles to
  origin; it needs a correction/refinement once the fix lands, since that's no longer true unconditionally.

**Out of scope:**
- Recovering the two already-affected live PRs (#82, #85) — per current GitHub state both are already
  merged via separate follow-up PRs (#88 `resolve merge conflict on PR #82`, #89 `resolve merge conflict
  on PR #85`), so no manual recovery action remains outstanding.
- A finalizer/watchdog that detects "resolver ran but the PR head didn't move" (the report's suggested
  secondary follow-up) — valuable but a separate, larger piece of work (a new `TaskSource`-side check),
  not needed once the root cause is fixed at the source.
- Any change to `GithubMergeabilityWatcher`, `dedup_key`, or the dedup mechanism itself — it is not broken,
  it correctly reflects that the head never moved; fixing the head-move restores its correctness.
- `ResolveConflictBehavior` / `LandingBehavior` themselves — neither needs to change; the bug is entirely
  in the shared driver they call.
- The initial `git worktree add --force` create-path reconciliation (invariant 30/31's "create" branch) —
  by construction the original task always pushes before its own `land` step runs, so a brand-new
  resolver worktree can never inherit a local-only commit from that shared ref; only the reattach path is
  broken.

## Rough plan

1. **Sync first.** `git fetch origin && git merge origin/main` into this task's branch — mandatory, since
   the resolver feature (and the buggy code) doesn't exist yet on this stale branch; nothing below can be
   implemented or tested without it.
2. **Write the failing regression test (FR-4)** in `tests/test_git_workspace.py`, following the existing
   real-git fixture conventions (`_workspace_with_remote`, `_make_task`/override `Task`, a bare remote).
   Confirm it fails against the current code (reproducing the reported bug), matching this repo's
   TDD-with-verified-red convention seen in prior commits on this file (`cfc2707`).
3. **Implement the ancestry check** in `GitWorkspace.attach()`'s `elif override:` branch: fetch
   `origin/<branch>`, then branch three ways via `git merge-base --is-ancestor`:
   - local `HEAD` is an ancestor of (or equal to) `origin/<branch>` → reset `--hard` to `origin/<branch>`
     + `clean -fd` (today's behavior, unchanged — FR-2).
   - `origin/<branch>` is an ancestor of local `HEAD` (strictly behind) → leave `HEAD` alone; `clean -fd`
     is still safe (removes only untracked cruft, never touches commits) — FR-1.
   - neither is an ancestor of the other (diverged) → raise `GitError` naming the branch and both SHAs,
     no mutation — FR-3.
4. **Add the FR-1/FR-3 unit-level tests** in `tests/test_git_workspace.py`, alongside FR-4's end-to-end one.
5. **Run the full suite** (`.venv/bin/pytest -q`) and confirm the four pre-existing override-reattach
   tests (FR-2) still pass unmodified, plus `test_mergeability_e2e.py` (fake-driven, should be unaffected)
   and `test_resolve_conflict_behavior.py`.
6. **Update CLAUDE.md invariant 31** to describe the ancestry-aware reconciliation instead of the
   unconditional reset, and add a short note distinguishing it from invariant 28 (branch override exists)
   and invariant 30 (create-path `--force` reuse, unaffected).
7. **Land**: commit, push, PR — closing GitHub issue #86 (`Closes #86` in the PR body, matching this
   repo's existing convention for issue-sourced tasks).

## Open questions

- **Q1 — Is FR-5 (teaching `MemoryWorkspace` to model reset-on-reattach) worth doing now, or deferred?**
  Default: defer. FR-4's real-git test is the authoritative regression coverage the bug report itself
  asks for; extending the in-memory fake is a nice-to-have for faster/broader e2e coverage of *future*
  workspace-reset bugs, not required to close #86.
- **Q2 — Should the diverged case (FR-3) attempt any automatic recovery** (e.g., discard-and-let-a-fresh-
  resolver-task re-run, since a real origin advance changes the PR's head SHA and the watcher's dedup key
  with it) **instead of just failing the task?** Default: just fail (raise) for now — simplest, safest,
  and matches "never silently drop work." Automatic recovery can be layered on later (this is exactly the
  kind of case a watchdog/finalizer, called out as future work, would be better positioned to arbitrate).
  This is a genuinely rare race (both a local resolve-in-flight commit and an independent server-side
  branch advance in the same short window) — not worth over-engineering the first fix around.
- **Q3 — Exact wording of invariant 31's replacement** is left to the design step; this plan only commits
  to the requirement that the invariant be corrected, not to specific prose.
