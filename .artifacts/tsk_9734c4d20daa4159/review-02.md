# Review round 2 — mergeability watcher (auto-rebase "behind" PRs, queue conflicts to a resolver step)

Reviewed `development-02.md` against `review-01.md`'s single blocking issue,
re-read the actual diff (`git show 06cfacb`), and re-ran the full suite.
Round 1 (`ec28cf9`) was already confirmed sound except for this one bug; this
round only needed to fix it, and did.

## Verdict: done

## What was required

`review-01.md` blocked on: `GitWorkspace.attach`'s branch-override "reuse
existing local branch" path force-checks out the shared local
`refs/heads/<branch>` without reconciling it against `origin/<branch>`.
Because `GithubMergeabilityWatcher.update_branch` (FR-2) advances a PR branch
server-side via the GitHub API — no local git operation anywhere touches it —
the exact `behind` → `update_branch` → `dirty` → resolver sequence FR-2 AC3
anticipates left the resolver's worktree stale, and `land`'s plain (no-force,
invariant 25) push was rejected as non-fast-forward.

## What changed

`src/harness/drivers/git_workspace.py`: immediately after the `--force`d
`worktree add` that reuses the existing local branch, the new worktree is
hard-reset to `origin/<branch>`'s already-fetched tip:

```python
_git(["-C", str(base), "worktree", "add", "--force", str(worktree), branch])
_git(["-C", str(worktree), "reset", "--hard", f"origin/{branch}"])
```

Verified this is correctly ordered and scoped:
- The preceding `git fetch origin <branch>` (already present, one line
  earlier) runs unconditionally before the `_branch_exists_locally` check, so
  `origin/<branch>` is current by the time the reset runs.
- The reset targets the branch *as checked out in the newly created
  worktree*, not via a porcelain ref-update command from `base` — so it isn't
  blocked by git's "can't force-reset a branch checked out elsewhere" guard,
  matching invariant 26's own diagnosis of that guard and confirmed correct
  by directly reading the code path.
- The no-override path (every non-resolver task, invariant 24) and the
  no-local-copy fallback (fresh checkout from `origin/<branch>`, already
  correct by construction) are untouched.

`CLAUDE.md` invariant #27 documents the fix and why it must not be dropped;
`git_workspace.py`'s module docstring was updated to match.

## Verification performed this round

- Read the full diff of `06cfacb` (the fix + regression test + invariant).
- Ran the full suite: **518 passed, 1 opt-in skip** (`test_smoke_claude.py`,
  unaffected), matching the round's claim.
- Ran `tests/test_architecture.py` alone (14/14) — the import-boundary guards
  (invariants 1, 5, 11, 17, 20, 23) are unaffected by a change confined to a
  single driver file.
- Independently reproduced the review's falsifiability claim rather than
  trusting the summary: swapped `git_workspace.py` back to the pre-fix
  (`a2d0009`) version and re-ran
  `test_attach_with_branch_override_reconciles_stale_local_ref_with_origin`
  — it fails exactly as described (`FileNotFoundError` on the server-side
  file, i.e. the resolver's worktree never got the server-side commit).
  Restored the fixed file (confirmed via `git diff --stat` showing no
  residual changes) and re-ran the full suite — 518 passed again.
- Confirmed the existing override tests
  (`test_attach_with_branch_override_force_checks_out_already_checked_out_branch`,
  `..._creates_from_origin_when_no_local_copy`) are untouched and still
  cover the already-in-sync case unchanged.

## Everything from round 1 (re-confirmed unaffected)

`GithubClient` PR/update-branch verbs, `WorkspaceHandle.merge`,
`ResolveConflictBehavior`'s clean-merge vs. conflict-then-agent fork,
`GithubMergeabilityWatcher`, `column_order`/`BoardProjection`'s multi-workflow
board fix, dispatcher/consumer untouched (invariants 1-3 hold), `cli.py`
wiring, and invariants 24-26 — none of this round's changes touch any of
that surface, and the diff (`06cfacb --stat`) confirms only
`git_workspace.py`, its test file, `CLAUDE.md`, and this task's own artifacts
changed.

## Non-blocking note (not requested changes)

`review-01.md`'s point 3 ("optional but recommended": a full
`behind`→`update_branch`→`dirty`→resolver→`land` real-git smoke test driven
through `GithubMergeabilityWatcher` itself, rather than reproducing the bug
directly against `GitWorkspace.attach`) was explicitly not done, with a
reasoned justification (the direct test isolates the actual bug with less
machinery; `test_mergeability_e2e.py` already covers the watcher's routing
logic against fakes). That's a defensible call on an item the prior review
itself marked optional — not a basis for another round.

```json
{"outcome": "done", "summary": "Round 2 fixes the sole blocking issue from review-01.md: GitWorkspace.attach's branch-override reuse path now hard-resets the new worktree to origin/<branch>'s tip, reconciling the stale shared local ref left behind by GithubMergeabilityWatcher's server-side update_branch. Verified the fix directly (reverted it, confirmed the new regression test fails pre-fix and passes post-fix) and confirmed the rest of round 1 is unaffected. Full suite: 518 passed, 1 opt-in skip."}
```
