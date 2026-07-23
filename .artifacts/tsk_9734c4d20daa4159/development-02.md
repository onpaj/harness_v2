# Development round 2 — fix the branch-override staleness bug from review-01.md

`review-01.md` returned `request_changes` with one blocking issue: everything
from round 1 (`GithubClient` PR/update-branch verbs, `WorkspaceHandle.merge`,
`ResolveConflictBehavior`, `GithubMergeabilityWatcher`, the multi-workflow
`build()`/`BoardProjection` wiring, `cli.py` wiring) was confirmed sound and
is unchanged this round. This round addresses only the blocking issue.

## The bug

`GitWorkspace.attach`'s branch-override "reuse the existing local branch" path
(`src/harness/drivers/git_workspace.py`, the `override` + `_branch_exists_locally`
branch) force-checked out the **shared local** `refs/heads/<branch>` into the
resolver's new worktree completely as-is. That local ref only stays in sync
with `origin/<branch>` as long as every advance of the branch goes through a
local commit+push somewhere — but `GithubMergeabilityWatcher`'s own `behind`
handling (`update_branch`) advances the branch **server-side**, via the GitHub
API, touching no local git state at all. So the exact sequence the spec's own
FR-2 AC3 anticipates (`behind` → `update_branch` → possibly `dirty` again on a
later poll → resolver task on the same branch) left the resolver's worktree
missing the server-side commit; `land`'s later plain `git push -u origin
<branch>` (no force, by design — invariant 25) was then rejected as
non-fast-forward, failing the resolver task into `failed/` for no real reason.

The reviewer verified this empirically against real git (a second clone
pushing directly to origin, bypassing the base repo's local refs entirely,
then reproducing the override path and the resulting rejected push). I
reproduced the same scenario as an automated test (below) before writing the
fix, confirming the failure, then confirming the fix resolves it.

## The fix

`src/harness/drivers/git_workspace.py`, in `GitWorkspace.attach`'s override
branch: immediately after the `--force`d `worktree add` that reuses the
existing local branch, the new worktree is hard-reset to `origin/<branch>`'s
already-fetched tip:

```python
_git(["-C", str(base), "worktree", "add", "--force", str(worktree), branch])
_git(["-C", str(worktree), "reset", "--hard", f"origin/{branch}"])
```

This is safe and matches the reviewer's suggested direction: git's "refuses
to force-reset a branch checked out elsewhere" guard (the reason `-B` doesn't
work from `base`, per round 1's correction) does not apply here — the reset
runs *inside* the newly created worktree, targeting the branch as checked out
in *that* worktree, not "elsewhere." The worktree was just created, so there's
no local-only work in it to lose; `origin/<branch>` was already fetched by
the preceding `git fetch origin <branch>` (worktrees of the same repo share
remote-tracking refs), so no second fetch is needed.

The fallback branch (no local copy — created fresh via `-b branch
origin/branch`) was already correct and untouched: a fresh checkout from
`origin/<branch>` starts at the current tip by construction.

Updated the module docstring in `git_workspace.py` and added `CLAUDE.md`
invariant #27 documenting why the reset is necessary and that it must not be
dropped.

## Regression test

`tests/test_git_workspace.py::test_attach_with_branch_override_reconciles_stale_local_ref_with_origin`
reproduces the reviewer's exact scenario against real git:

1. Attach + commit + push a task branch (the "original task" worktree).
2. Advance `origin/<branch>` through a **separate clone** that pushes directly
   — simulating `update_branch`'s server-side merge, never touching the base
   repo's local refs.
3. Attach a resolver task with the branch override.
4. Assert the resulting worktree reflects origin's new tip (the file added by
   the "server-side" commit is present; `HEAD` equals `origin/<branch>`'s SHA).
5. Commit a further change and `push()` — asserting it succeeds (this is the
   step that was rejected as non-fast-forward before the fix; I confirmed the
   test fails with the pre-fix code and passes after).

The existing override tests
(`test_attach_with_branch_override_force_checks_out_already_checked_out_branch`,
`..._creates_from_origin_when_no_local_copy`) are untouched and still pass —
they cover the already-in-sync case, which the fix leaves unchanged.

I did not add the review's third, explicitly "optional" suggestion (a full
`behind`→`update_branch`→`dirty`→resolver→`land` real-git smoke test wired
through `GithubMergeabilityWatcher` itself) — the new `test_git_workspace.py`
test isolates and directly proves the actual bug (the stale-ref reconciliation
in `attach`) with much less machinery, and the existing
`tests/test_mergeability_e2e.py` already covers the watcher's own dirty/behind
routing logic against fakes.

## Files changed
- `src/harness/drivers/git_workspace.py` — the fix + docstring update.
- `tests/test_git_workspace.py` — new regression test.
- `CLAUDE.md` — new invariant #27.

## How to verify
```sh
.venv/bin/pytest -q
```
518 passed, 1 skipped (`test_smoke_claude.py`, opt-in real-`claude` smoke,
unaffected) — one more passing test than round 1 (the new regression test).
All real-git smoke suites (`test_smoke_git.py`, `test_smoke.py`,
`test_smoke_github.py`) and `test_architecture.py`'s import-boundary guards
are green.

To see the bug reproduced directly, `git stash` just the `git_workspace.py`
change and re-run:
```sh
git stash -- src/harness/drivers/git_workspace.py
.venv/bin/pytest -q tests/test_git_workspace.py::test_attach_with_branch_override_reconciles_stale_local_ref_with_origin
git stash pop
```
(fails with a rejected non-fast-forward push before the fix, passes after).
