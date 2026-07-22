# Review — mergeability watcher (auto-rebase "behind" PRs, queue conflicts to a resolver step)

Reviewed against `plan-01.md`, `design-01.md`, `architecture-01.md`, the actual
diff (`git log`/`git show` of the implementation commit), and the current test
suite (`517 passed, 1 skipped`).

## Verdict: request_changes

The overall shape is right and matches the design/architecture docs closely —
`GithubClient` PR/update-branch verbs, `WorkspaceHandle.merge`,
`ResolveConflictBehavior`, `GithubMergeabilityWatcher`, the `build()`/
`BoardProjection` multi-workflow wiring, and the dev step's own correction to
architecture-01.md's broken `-B ... --force` recipe are all sound and verified
against real git. But there is a second, unaddressed git-staleness bug in the
same code path the dev step just fixed — one that breaks a scenario the spec
itself requires (FR-2 AC3 combined with FR-3).

## Blocking issue

**`GitWorkspace.attach`'s branch-override "reuse the existing local branch"
path can check out a local ref that is stale relative to `origin/<branch>`,
causing the resolver's `land` step to fail with a rejected (non-fast-forward)
push — in a scenario the spec explicitly anticipates, not a hypothetical
edge case.**

`src/harness/drivers/git_workspace.py:192-203`: when `_branch_exists_locally`
is true, `attach` does:

```python
_git(["-C", str(base), "fetch", "origin", branch])
if _branch_exists_locally(base, branch):
    _git(["-C", str(base), "worktree", "add", "--force", str(worktree), branch])
```

`git fetch origin <branch>` updates the remote-tracking ref
(`refs/remotes/origin/<branch>`) but never touches the **local**
`refs/heads/<branch>` — and per the docstring's own correct diagnosis, that
ref can't be force-reset here (`git branch -f`/`fetch origin
<branch>:<branch>` both refuse while the branch is checked out in another
worktree — I verified this too). So the code falls back to reusing
`refs/heads/<branch>` completely as-is, with no attempt to reconcile it
against `origin/<branch>`.

That local ref is shared across every worktree of the same repo, so it does
stay in sync as long as the harness is the only thing that ever advances the
branch **through a local commit** (worktree commits update the shared ref
immediately, regardless of which worktree made them — confirmed by reading
`commit()`). But **`GithubMergeabilityWatcher`'s own "behind" handling
(FR-2) advances the branch a different way**: `update_branch` merges base
into head **server-side**, via the GitHub API, with no local git operation at
all. Nothing in this codebase ever fetches that server-side merge commit back
into the shared local ref. FR-2's own AC3 says: *"An update call that itself
produces a conflict is treated as `dirty` on the next poll"* — i.e., the
spec explicitly expects the sequence *behind → `update_branch` → (possibly)
dirty on a later poll → resolver task on the same branch* to work. When that
happens, `attach`'s reuse of the stale local ref means the resolver's
worktree is missing the server-side update commit; when `land` later runs a
plain `git push -u origin <branch>` (`GitWorkspaceHandle.push`, no force by
design — invariant 25), the push is rejected as non-fast-forward, and the
resolver task fails into `failed/` even though nothing is actually wrong with
the conflict resolution itself.

I verified this empirically end-to-end with real git (not just static
reading): created a branch, pushed it (simulating the original task), then
simulated GitHub's server-side `update-branch` via a *separate* clone pushing
directly to origin (never touching the first repo's local refs) — then
reproduced `attach`'s override path (`worktree add --force <path> <branch>`
reusing the now-stale local ref), ran `merge()` against the fetched base, and
pushed:

```
$ git push -u origin harness/x1
To ../remote.git
 ! [rejected]        harness/x1 -> harness/x1 (fetch first)
error: failed to push some refs to '../remote.git'
```

This matches the exact resolver flow (`attach` → `merge` → agent/commit →
`push`) that `ResolveConflictBehavior`/`LandingBehavior` drive.

**Why this survived the test suite:** `tests/test_git_workspace.py`'s new
override tests (`test_attach_with_branch_override_force_checks_out_
already_checked_out_branch`, `..._creates_from_origin_when_no_local_copy`)
only cover the case where the local ref and origin are already in sync at
attach time — they never advance origin independently of the local repo
first. The e2e coverage (`tests/test_mergeability_e2e.py`) exercises the
`behind`→`update_branch` path and the `dirty`→resolver path only as two
*separate* PRs on `MemoryWorkspace` (a fake with no real git semantics), so
the one interaction that actually breaks — the same PR transitioning
`behind` (auto-updated) into `dirty` (resolver dispatched) — is never
exercised against real git anywhere in the suite.

**What needs to change:** the override path needs to reconcile the local
worktree with `origin/<branch>`'s actual tip before the resolver starts
working, not just silently reuse whatever local ref happens to exist. (One
viable direction, since the "checked out elsewhere" restriction only blocks
porcelain ref-update commands, not operations run *inside* the newly created
worktree itself: after `worktree add --force ... <branch>`, run `git fetch
origin <branch>` from inside the *new* worktree and `git reset --hard
origin/<branch>` there, before `merge()` runs — that worktree isn't "checked
out elsewhere" from its own point of view. Whatever the mechanism, it needs a
real-git test that advances `origin/<branch>` independently of the local repo
between the original attach and the resolver's attach, then asserts the
resolver's final push succeeds.)

## Everything else: confirmed sound

- `PullRequestInfo`/`list_pull_requests`/`update_branch` on `GithubClient`
  (both fakes and `HttpGithubClient`'s two-tier list+detail fetch) match
  design-01.md and add no new dependency.
- `WorkspaceHandle.merge` and `ResolveConflictBehavior`'s clean-merge vs.
  conflict-then-agent fork match FR-4 exactly; invariant 9 (worker commits)
  is honored in both branches.
- `LandingBehavior` reused unmodified; idempotent PR resolution by branch is
  unaffected by this feature (confirmed by reading `github_forge.py`).
- `column_order`/`BoardProjection` and `build()`'s union of step queues
  across workflows correctly fix the "resolver tasks vanish from the board"
  bug architecture-01.md flagged.
- Dispatcher/consumer needed zero changes, correctly — confirmed no branch on
  `task.workflow_template` was added to either file (invariants 1-3 hold).
- `cli.py` wiring (`_mergeability_sources`, `--watch-mergeability`,
  `--resolver-workflow`, `_init()` writing `resolver.json`/`agents/
  resolve.json`) mirrors the existing `_github_sources` pattern as specified,
  and correctly drops design-01.md's reference to a nonexistent
  `--github-repo` flag per architecture-01.md's correction.
- `CLAUDE.md` invariants 24-26 are added and accurately describe the
  implemented behavior (including invariant 26's own correct diagnosis of the
  `-B`/`--force` distinction) — though invariant 26 as written doesn't yet
  cover the staleness gap above; it should be revisited once the fix lands.
- Full test suite passes (517 passed, 1 opt-in skip unaffected).

## What to fix for the next round

1. Fix `GitWorkspace.attach`'s override path so the worktree it hands to
   `ResolveConflictBehavior` reflects `origin/<branch>`'s actual current tip,
   not just whatever the shared local ref happened to be last time any
   worktree of this repo committed to it.
2. Add a real-git regression test (in `tests/test_git_workspace.py`, next to
   the existing override tests) that advances `origin/<branch>` through a
   path that does **not** go through the local repo's shared ref (mirroring
   `update_branch`'s server-side nature — e.g. a second clone pushing
   directly, as I did above) between the original attach/push and the
   resolver's attach, then asserts the resulting worktree is based on
   origin's tip (or at minimum, that a subsequent push succeeds).
3. Optional but recommended: extend `tests/test_mergeability_e2e.py` (or a
   new real-git smoke test alongside `test_smoke_git.py`) to exercise the
   exact `behind` → `update_branch` → `dirty` → resolver → `land` sequence on
   the *same* PR, since that's the interaction FR-2/FR-3 together promise and
   the one the current suite never drives end-to-end.
