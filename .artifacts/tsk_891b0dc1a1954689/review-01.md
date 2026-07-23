# Review — ancestry-aware reconciliation in `GitWorkspace.attach()`'s override reattach path

Reviewed commit `38becdd` against plan-01.md / design-01.md / architecture-01.md / development-01.md and the
task spec (#86: `land`'s reattach silently discarding `resolve`'s un-pushed merge commit).

## Conformance to spec

The fix matches the root cause exactly: the `elif override:` reattach branch in
`src/harness/drivers/git_workspace.py` previously did an unconditional `reset --hard origin/<branch>`,
discarding any un-pushed local commit before `land` could push it. It now calls
`_reconcile_override_reattach(base, worktree, branch)`, which does a three-way `git merge-base
--is-ancestor` check:

- local `HEAD` behind or equal to `origin/<branch>` → reset to origin (unchanged — resolver retry / server-side
  `update_branch` advance, nothing local to lose).
- `origin/<branch>` behind local `HEAD` (local ahead) → leave `HEAD` untouched — preserves the `resolve` step's
  un-pushed merge commit for `land` to push.
- neither an ancestor of the other (genuine divergence) → raises `GitError` naming both SHAs, before any
  mutating git call.

This is precisely the proposed fix in the task description and plan/design/architecture artifacts.

## Adherence to architecture

- No port or signature changes: `Workspace`/`WorkspaceHandle` (`ports/workspace.py`) are untouched, so
  `ResolveConflictBehavior` and `LandingBehavior` need no changes.
- `GitError` is a `RuntimeError` subclass, caught by `consumer.py`'s existing broad `except Exception` handler
  (`consumer.py:77-78`), which fails the task into `failed/` itself — this is exactly invariant #3's carve-out
  (behavior raises → consumer writes terminal `failed`), with no new outcome-dependent branching introduced
  into `consumer.py` or `dispatcher.py`.
- `clean -fd` still runs unconditionally after the ancestry check in all three branches — safe, since it only
  touches untracked files, never commits or the ref.

## Completeness

- Two new real-git regression tests added to `tests/test_git_workspace.py`:
  `test_attach_reattach_with_override_preserves_unpushed_local_commit` (the literal repro: `resolve` commits
  locally, `land` reattaches, asserts `HEAD` survives and `push()` actually advances `origin/<branch>`) and
  `test_attach_reattach_with_override_raises_on_genuine_divergence` (independent local + server-side advances,
  asserts `GitError` and that neither side moved). Per the dev log both were confirmed red against the
  pre-fix code before the fix landed.
- Existing four override-reattach tests left unmodified as regression guard — confirms the create-path and
  non-divergent reattach paths are behavior-preserved.
- `CLAUDE.md` invariant 31 updated to describe the new ancestry-aware reattach behavior and explicitly warns
  against collapsing it back to an unconditional reset, citing `#86`.
- Out-of-scope items (recovering live PRs #82/#85, a watchdog/finalizer) correctly left untouched, as called
  out in plan-01.md/architecture-01.md.

## Correctness — verified independently

- Ran `.venv/bin/pytest -q tests/test_git_workspace.py`: 19 passed.
- Ran the full suite `.venv/bin/pytest -q`: 1218 passed, 1 skipped, 0 failed — matches the development log,
  no regressions.
- Read the diff directly: the ancestry check reads `local_head`/`origin_head` via `rev-parse` before any
  mutation, so the divergence branch raises without side effects, matching the test's assertion that neither
  side moved on a raise.

## Verdict

No functional requirement unmet, no architecture violation, no missing required test, no correctness bug
found. Implementation is sound and minimal.
