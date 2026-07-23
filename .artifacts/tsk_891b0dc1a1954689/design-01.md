# Design — ancestry-aware reconciliation in `GitWorkspace.attach()`'s override reattach path

No UI surface: this is entirely inside `src/harness/drivers/git_workspace.py`, a driver behind the
`Workspace` port. `api/`/`projection.py` never see it (invariant 5/11). This design covers the component
change, its data/control shapes, and the test surface — no wireframes.

## 1. Component design

### 1.1 Boundary — unchanged

`GitWorkspace.attach(task: Task) -> GitWorkspaceHandle` keeps its exact signature and remains the only
entry point `ResolveConflictBehavior` and `LandingBehavior` call. The fix is confined to the `elif
override:` branch (currently `git_workspace.py:244-258`, shown below) — the `if not worktree.exists():`
create branches and the non-override `else:` reattach branch are untouched, per FR-2's non-goal.

```python
elif override:
    _git(["-C", str(base), "fetch", "origin", branch])
    _git(["-C", str(worktree), "reset", "--hard", f"origin/{branch}"])
    _git(["-C", str(worktree), "clean", "-fd"])
```

### 1.2 New internal helper — `_reconcile_override_reattach`

A single private function, colocated with the existing `_git`/`_branch_exists_locally` helpers, keeps
`attach()`'s own body a flat sequence of branches (matching its current style) instead of growing a nested
`if` inside the `elif`:

```python
def _reconcile_override_reattach(base: Path, worktree: Path, branch: str) -> None:
    """Reattach an override (resolver) worktree, preserving any un-pushed local
    commit instead of blindly resetting to origin.

    Three-way ancestry check via `git merge-base --is-ancestor`:
      - local HEAD is an ancestor of (or equal to) origin/<branch>: nothing
        local to lose — reset to origin (today's behavior, e.g. a resolver
        retry with no commit yet, or update_branch's server-side advance).
      - origin/<branch> is an ancestor of local HEAD: a real, un-pushed local
        commit is sitting ahead (the resolve -> land hand-off) — keep it,
        untouched.
      - neither is an ancestor of the other: both sides advanced
        independently since the worktree was last reconciled — raise rather
        than silently pick a side.
    """
    _git(["-C", str(base), "fetch", "origin", branch])
    local_head = _git(["-C", str(worktree), "rev-parse", "HEAD"]).strip()
    origin_head = _git(["-C", str(base), "rev-parse", f"origin/{branch}"]).strip()

    if local_head == origin_head or _is_ancestor(worktree, local_head, origin_head):
        _git(["-C", str(worktree), "reset", "--hard", f"origin/{branch}"])
    elif _is_ancestor(worktree, origin_head, local_head):
        pass  # local is ahead — nothing to reconcile, HEAD stays as-is
    else:
        raise GitError(
            f"branch {branch!r} diverged on reattach: local HEAD {local_head} and "
            f"origin/{branch} {origin_head} share no ancestry — refusing to guess "
            "which side to keep"
        )
    _git(["-C", str(worktree), "clean", "-fd"])
```

`_is_ancestor` is a thin wrapper around `git merge-base --is-ancestor`, which exits `0`/`1` (not an
error) rather than raising — it cannot go through `_git()`'s `check=True` path:

```python
def _is_ancestor(repo: Path, maybe_ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", maybe_ancestor, descendant],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
```

`attach()`'s `elif override:` branch shrinks to a single call:

```python
elif override:
    _reconcile_override_reattach(base, worktree, branch)
```

`clean -fd` stays unconditional in all three outcomes — it only removes untracked files, never touches a
commit, so it's safe even on the "keep local HEAD" path (FR-1's AC1 needs the working tree clean, not just
the ref).

### 1.3 Responsibility split (unaffected)

- `GitWorkspace`/`GitWorkspaceHandle` — unchanged. `attach()` still returns a handle; `handle.commit()` /
  `handle.push()` are untouched by this fix.
- `ResolveConflictBehavior` — unchanged. It still attaches, merges, lets the agent resolve, and commits
  locally. It has no opinion on reconciliation; that's the workspace driver's job (invariant 9 stays
  intact: the behavior still doesn't push).
- `LandingBehavior` — unchanged. It still re-`attach()`es and calls `handle.push()`; the fix makes that
  second `attach()` stop discarding what the first one produced.
- `consumer.py` — unchanged. `GitError` raised from `_reconcile_override_reattach` propagates out of
  `LandingBehavior.run()` exactly like any other behavior exception; the consumer's existing catch-all
  (invariant 3) writes the task into `failed/` with the exception's message as the history reason. No new
  branch on outcome value is introduced anywhere in `consumer.py`.

### 1.4 Control flow — sequence for the bug's exact shape (FR-4)

```
resolve step                          land step
─────────────                         ──────────
attach(task)                          attach(task)              [reattach, override branch]
  worktree exists (reuse path)          worktree exists
  → _reconcile_override_reattach        → _reconcile_override_reattach
    local HEAD == origin HEAD             local HEAD (merge commit) ahead of origin HEAD
    → reset --hard origin (no-op)         → is_ancestor(origin_head, local_head) == True
handle.merge(base)  → conflict            → HEAD left untouched, only `clean -fd` runs
agent resolves
handle.commit(...)  → creates the       handle.push()
                       2-parent merge      → origin/<branch> fast-forwards to the merge commit
                       commit, LOCAL     forge.open_pull_request(...)
                       ONLY                → existing PR's head is now the merge commit;
                                             GithubMergeabilityWatcher's dedup_key changes on
                                             next detection → no longer stuck
```

### 1.5 Divergence path (FR-3) — who can actually hit it

Both sides moving independently between `resolve`'s commit and `land`'s reattach requires
`GithubMergeabilityWatcher.update_branch` to run and push a server-side merge to the same branch in that
narrow window — genuinely rare (Q2 in the plan; deferred, not engineered around). When it happens:
`attach()` raises `GitError` with both SHAs and the branch name in the message (per the plan's
auditability NFR) → `LandingBehavior.run()` propagates it unmodified → `consumer.py`'s existing exception
handling drops the task into `failed/`, visible on the board with that message as the reason. No new
recovery path is designed here; a human (or a future watchdog, out of scope) re-queues a fresh resolver
task, which starts clean because it'll fetch and observe the new origin tip.

## 2. Data schemas

No schema changes anywhere in this fix.

- `Task`, `BehaviorResult`, `HistoryEntry` — untouched. `GitError`'s message is the only new "payload,"
  and it flows through the existing untyped exception → history-reason string path `consumer.py` already
  has for every other behavior failure.
- No new event payload, no new artifact shape, no new port method. `Workspace`/`WorkspaceHandle` (`ports/
  workspace.py`) keep their current interface — `attach`, `write`, `commit`, `push`, `merge` — the fix adds
  no new method to the port, since the ancestry check is purely internal reconciliation logic inside one
  driver's existing `attach()` call.

## 3. Test surface (design-level; task breakdown is the development step's job)

Grounded in the existing real-git fixtures in `tests/test_git_workspace.py` (`_workspace_with_remote`,
`_make_task`, `_git` helper) — no new fixture machinery needed, per the plan's NFR against a broader
rewrite:

- **FR-1/FR-4 regression** — reuses the exact `_workspace_with_remote` + override-`Task` shape from
  `test_attach_reattach_with_override_reconciles_with_origin_after_server_side_advance`, but instead of
  advancing origin via a second clone, it commits locally on the *already-attached* handle (mirroring
  `ResolveConflictBehavior.run()`'s `handle.merge()` + `handle.commit()`), then re-`attach()`es (mirroring
  `LandingBehavior.run()`'s call) and asserts `HEAD` is unchanged, then `push()` and asserts
  `origin/<branch>` advanced to that SHA — this is FR-4's end-to-end shape and FR-1's AC1/AC2 in one test.
- **FR-2 regression** — the four existing override-reattach tests run unmodified against
  `_reconcile_override_reattach`; no test code changes, only the production code under them.
- **FR-3** — a new test: attach override, commit locally (nothing pushed), then advance `origin/<branch>`
  independently via a second clone (same technique the existing "server-side advance" test uses), then
  reattach and assert `pytest.raises(GitError)`, and that neither `HEAD` nor `origin/<branch>` moved as a
  result (the raise happens before any mutating git call in the diverged branch).

## 4. CLAUDE.md invariant 31 — replacement wording (Q3)

Current text (`CLAUDE.md:74` on `origin/main`) says the reused ref is unconditionally hard-reset to
`origin/<branch>` on reattach. Replace the reattach-specific half of that sentence (the create-path
reconciliation it also describes stays correct and unchanged — FR-2/out-of-scope) with:

> On reattach, this reconciliation is ancestry-aware: `GitWorkspace.attach` hard-resets to
> `origin/<branch>` only when local `HEAD` is behind or equal to it (nothing local to lose); when local
> `HEAD` is ahead — the `resolve → land` hand-off producing an un-pushed merge commit in the same worktree
> — it's left untouched; if the two have diverged, `attach` raises rather than silently discarding either
> side. Don't collapse this back to an unconditional reset — that's the bug `#86` fixed.
