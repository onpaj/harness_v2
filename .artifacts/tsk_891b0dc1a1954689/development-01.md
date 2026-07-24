# Development — ancestry-aware reconciliation in `GitWorkspace.attach()`'s override reattach path

Implements plan-01.md / design-01.md / architecture-01.md as ratified, closing #86 (resolver's un-pushed
merge commit silently discarded by `land`'s reattach).

## Prerequisite: synced branch onto `origin/main`

This task's branch was 69 commits behind `origin/main` (the resolver feature the bug lives in didn't exist
here yet). Per architecture-01.md's mandatory prerequisite: fetched and merged `origin/main` — confirmed
clean beforehand via `git merge-tree`, and the merge itself produced zero conflicts (merge commit
`3156603`). All file references below are against the now-current code.

## Changes

### `src/harness/drivers/git_workspace.py`

- Added `_is_ancestor(repo, maybe_ancestor, descendant)` — thin wrapper around `git merge-base
  --is-ancestor`, read via exit code (not `_git()`'s `check=True`, since "not an ancestor" is exit 1, a
  normal branch outcome, not a failure) — colocated with the existing `_branch_exists_locally` helper
  (same "raw `subprocess.run`, no wrapper" family).
- Added `_reconcile_override_reattach(base, worktree, branch)` — replaces the `elif override:` reattach
  branch's unconditional `reset --hard origin/<branch>` with a three-way ancestry check:
  - local `HEAD` behind or equal to `origin/<branch>` → reset to origin (unchanged behavior — a resolver
    retry, or `update_branch`'s server-side advance with nothing local to lose).
  - `origin/<branch>` behind local `HEAD` (local ahead) → leave `HEAD` untouched — this is the `resolve →
    land` hand-off: the un-pushed merge commit survives reattach.
  - neither an ancestor of the other (genuine divergence) → raise `GitError` naming both SHAs and the
    branch, before any mutating call — nothing is reset, nothing is discarded silently.
  - `clean -fd` still runs unconditionally afterward (removes only untracked files, never touches commits
    or the ref, so it's safe on every branch including "keep local HEAD").
- `GitWorkspace.attach()`'s `elif override:` body now delegates to `_reconcile_override_reattach(base,
  worktree, branch)` in place of the three inlined git calls. No signature change anywhere; `Workspace`/
  `WorkspaceHandle` (`ports/workspace.py`) are untouched, so `ResolveConflictBehavior` and `LandingBehavior`
  need no changes (invariant #1, #9).
- Updated the module docstring (previously described the reattach reconciliation as an unconditional reset
  to `origin/<branch>`) to describe the new ancestry-aware behavior.

### `tests/test_git_workspace.py`

Two new tests, both real-git (matching this file's existing convention — no `MemoryWorkspace` change, per
architecture-01.md's Q1 deferral), reusing the existing `_workspace_with_remote`/`_make_task`/`_git` helpers:

- `test_attach_reattach_with_override_preserves_unpushed_local_commit` — the literal repro of the bug: the
  `resolve` step attaches, writes and commits locally (no push); the `land` step re-attaches the same
  override worktree. Asserts `HEAD` after reattach still equals the local merge commit (not reset away),
  then asserts `push()` actually advances `origin/<branch>` to that commit. **Confirmed red against the
  pre-fix code** (`HEAD` came back as the stale origin SHA instead of the merge commit) before implementing
  the fix, then green after.
- `test_attach_reattach_with_override_raises_on_genuine_divergence` — local `HEAD` advances (an un-pushed
  resolve commit) while `origin/<branch>` independently advances via a second clone (simulating
  `update_branch`). Asserts the reattach raises `GitError`, and that neither the worktree's `HEAD` nor the
  bare remote's actual branch tip moved as a result. **Confirmed red** (no exception raised — the old code
  silently reset over the local commit) before the fix.

Both use the existing four override-reattach tests unmodified as the regression guard (per FR-2's
non-goal) — none of them needed a single line changed; all still pass, confirming the create-path and the
non-divergent reattach paths are byte-for-byte behavior-preserved.

### `CLAUDE.md`

Extended invariant 31 (previously scoped only to the create-path's reuse-of-local-branch reconciliation) to
also describe the reattach path's new ancestry-aware behavior, ending with an explicit anti-regression note
citing `#86` — per design-01.md §4 / architecture-01.md's Q3, so a future reader doesn't "simplify" this
back to an unconditional reset.

## Test results

```
.venv/bin/pytest -q tests/test_git_workspace.py
19 passed in ~15-30s (real git, no in-memory fakes)

.venv/bin/pytest -q tests/test_mergeability_e2e.py tests/test_resolve_conflict_behavior.py tests/test_architecture.py
34 passed

.venv/bin/pytest -q   (full suite)
1218 passed, 1 skipped (the opt-in HARNESS_SMOKE_CLAUDE test), 0 failed
```

## How to verify

```sh
cd /Users/rem/harness-root/worktrees/tsk_891b0dc1a1954689
.venv/bin/pytest -q tests/test_git_workspace.py -v -k "preserves_unpushed_local_commit or raises_on_genuine_divergence"
.venv/bin/pytest -q   # full suite
```

To see the fix is load-bearing, temporarily revert `_reconcile_override_reattach`'s body to the old
three-line unconditional reset and re-run
`test_attach_reattach_with_override_preserves_unpushed_local_commit` — it fails with `HEAD` equal to the
stale pre-merge SHA instead of the resolve commit, exactly reproducing the reported #82/#85 symptom.

## Out of scope (per plan-01.md / architecture-01.md, unchanged)

- Recovering the two already-stuck live PRs (#82, #85) by pushing their recoverable merge commits from the
  worktree reflogs — a manual/ops action, not a code change.
- A finalizer/watchdog that detects "resolver ran but the PR head never moved" — deferred as a separate
  follow-up.
- Auto-recovery on genuine divergence — `attach` raises and stays raising; a human or future watchdog
  re-queues a fresh resolver task.
