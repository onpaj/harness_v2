# Architecture assessment — ancestry-aware reconciliation in `GitWorkspace.attach()`

## Verdict

**Ratified, with the open questions in plan-01.md resolved.** I re-read `design-01.md` against the actual
`src/harness/drivers/git_workspace.py` and `tests/test_git_workspace.py` on `origin/main` (not this stale
branch — see Prerequisites) line-by-line: every quoted code block, line-range reference, and fixture name
in the design matches the real file verbatim. The three-way `merge-base --is-ancestor` check is the right
shape and the smallest correct fix. No architectural rework is needed; this document exists to (a) confirm
there is no gap between design and reality, (b) close the plan's three open questions with a decision
instead of leaving them for `development` to guess at, and (c) flag two risks the design didn't fully
carry through to implementation.

## 1. Alignment with existing patterns and integration points

- **Single point of change.** The fix lives entirely inside `GitWorkspace.attach()`'s `elif override:`
  branch (`git_workspace.py:244-258` on `origin/main`). It touches no port, no behavior, no consumer. This
  is the correct integration point: `Workspace` (`ports/workspace.py`) is the only abstraction
  `ResolveConflictBehavior` and `LandingBehavior` depend on, and its signature (`attach`, `write`, `commit`,
  `push`, `merge`) is untouched — a pure internal-to-driver change, consistent with invariant #1 ("swap a
  driver, never its surroundings") and invariant #9 (the behavior doesn't push; the driver decides
  reconciliation).
- **Failure propagation matches the existing contract.** `GitError` is already a plain `RuntimeError`
  raised from `_git()` on any failed subprocess call, and `LandingBehavior.run()` already lets a `push()`
  failure propagate uncaught ("A failure here raises, and the consumer writes the task into `failed/`" —
  the behavior's own docstring). Raising `GitError` from the new divergence branch needs zero new handling
  anywhere — it rides the exact same path invariant #3 already describes (consumer catches, writes
  `failed`, `lastOutcome` untouched). This is the one part of the design I checked hardest for a hidden
  new branch in `consumer.py`, since invariant #2 forbids the consumer branching on outcome — there is none;
  an uncaught exception is not an outcome value.
- **Test convention match.** `tests/test_git_workspace.py` is real-git, not in-memory, by design (CLAUDE.md:
  "Don't tidy them into an in-memory shape; that coverage would vanish" — stated for the smoke tests, but
  the same real-git convention holds for this file's override-reattach fixtures specifically because it's
  the only place a reset-vs-preserve bug like this one is even observable). `MemoryWorkspace` never models
  reset-on-reattach at all, so it structurally cannot regress-test this class of bug — the design correctly
  avoids extending it (Q1, below).
- **Docs-as-contract convention.** This repo treats CLAUDE.md invariants as load-bearing, checked-in
  behavior contracts (several are literally guarded by `tests/test_architecture.py`). Invariant 31 on
  `origin/main` currently asserts the unconditional reset as fact. Leaving it unedited after this fix would
  make the doc wrong in a way indistinguishable from "the fix wasn't actually applied" to the next reader —
  the plan is right to make the CLAUDE.md edit in-scope, not a follow-up.

## 2. Proposed architecture

No new component. The change is two private functions added to `git_workspace.py`, replacing three lines
of the `elif override:` body with one call:

```python
elif override:
    _reconcile_override_reattach(base, worktree, branch)
```

```python
def _reconcile_override_reattach(base: Path, worktree: Path, branch: str) -> None:
    _git(["-C", str(base), "fetch", "origin", branch])
    local_head = _git(["-C", str(worktree), "rev-parse", "HEAD"]).strip()
    origin_head = _git(["-C", str(base), "rev-parse", f"origin/{branch}"]).strip()

    if local_head == origin_head or _is_ancestor(worktree, local_head, origin_head):
        _git(["-C", str(worktree), "reset", "--hard", f"origin/{branch}"])
    elif _is_ancestor(worktree, origin_head, local_head):
        pass
    else:
        raise GitError(
            f"branch {branch!r} diverged on reattach: local HEAD {local_head} and "
            f"origin/{branch} {origin_head} share no ancestry — refusing to guess "
            "which side to keep"
        )
    _git(["-C", str(worktree), "clean", "-fd"])


def _is_ancestor(repo: Path, maybe_ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", maybe_ancestor, descendant],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
```

### Key decision — ancestry check via `merge-base --is-ancestor`, not commit-count or timestamp heuristics

**Options considered** (from plan-01.md, ratified here):

1. **`merge-base --is-ancestor` three-way branch** (chosen). Exit-code-based, no parsing, exactly answers
   "does the other side have anything I don't" in both directions. Matches the existing `_branch_exists_locally`
   helper's style (a `subprocess.run` without `check=True`, read via return code, sitting next to `_git`
   which *does* use `check=True`) — the codebase already has this exact two-helper pattern for "command
   whose non-zero exit is meaningful, not exceptional" vs. "command whose non-zero exit is a real failure."
   No new idiom introduced.
2. **Track whether `resolve` pushed, in `task.data`.** Rejected: would require `ResolveConflictBehavior` to
   know about push/no-push state (violates invariant #9 — the behavior still knows no git beyond
   merge/commit) and adds a data-model field for something git already answers authoritatively from the
   refs themselves. Two sources of truth for the same fact.
3. **Never reset on override reattach; always fast-forward-merge origin into local.** Rejected: silently
   changes behavior for the "local behind, nothing to lose" case (a resolver retry) from a clean reset to a
   merge commit that didn't exist before, and does nothing for genuine divergence — still needs a decision
   there. Strictly more moving parts than option 1 for no extra benefit.
4. **Skip the ancestry check; just try `push --force-with-lease` from `land` and let git's own protection
   decide.** Rejected outright: `attach()` for `resolve` would still reset first and destroy the commit
   before `land` even runs — the bug is in `attach`, not `push`, so a `push`-side fix can't reach it.

`_is_ancestor` deliberately does not go through `_git()` — `_git()`'s `check=True` would turn the expected
"not an ancestor" (exit 1) into a raised `GitError`, which is wrong: that outcome is a normal branch of the
three-way check, not a failure.

### Control flow for the bug's exact shape

```
resolve: attach() [reuse, local==origin → reset no-op] → merge (conflict) → agent → commit  [LOCAL ONLY]
land:    attach() [reuse, local ahead of origin → HEAD preserved, only clean -fd runs]
         → push() [fast-forwards origin to the merge commit]
         → open_pull_request() [existing PR's head now advances]
         → GithubMergeabilityWatcher's dedup_key changes on next detection → no longer stuck
```

## 3. Implementation guidance

- **File:** `src/harness/drivers/git_workspace.py`. Add `_is_ancestor` next to `_branch_exists_locally`
  (same "raw subprocess, no `_git()` wrapper" family); add `_reconcile_override_reattach` next to the other
  module-level helpers, above the `GitWorkspace` class. Replace the three lines in the `elif override:`
  branch with the single call shown above. **Do not touch** the `if not worktree.exists():` create branches
  or the non-override `else:` branch — both are explicitly out of scope (FR-2's non-goal) and already have
  passing coverage that must stay green unmodified.
- **Order of operations matters for FR-3's "no mutation on divergence" requirement.** The `raise` must occur
  before the trailing `_git(["-C", str(worktree), "clean", "-fd"])` call — the shape above already gets this
  right because `clean -fd` sits after the `if/elif/else`, so a `raise` inside `else` unwinds the function
  before reaching it. Keep it that way; don't hoist `clean -fd` into each branch individually, since that
  would risk running it before the raise in a future edit.
- **Test placement:** `tests/test_git_workspace.py`, alongside the existing override-reattach tests
  (`test_attach_with_branch_override_*`, `test_attach_reattach_with_override_reconciles_with_origin_after_server_side_advance`).
  Reuse `_workspace_with_remote`, `_make_task`, and the module's own `_git(args, cwd)` helper — no new
  fixture machinery, matching the plan's NFR against a broader rewrite. Concretely:
  - **FR-1/FR-4 (one test, combined):** attach override → on the *already-attached* handle, write+commit
    locally (mirrors `ResolveConflictBehavior.run()`'s `handle.merge()`+`handle.commit()`, no push) →
    re-`attach()` the same task (mirrors `LandingBehavior.run()`'s second `attach()`) → assert `rev-parse
    HEAD` in the reattached handle equals the pre-reattach commit SHA → `handle.push()` → assert
    `origin/<branch>` now equals that SHA. This is the literal reproduction of the reported bug: it must
    fail on current `origin/main` code and pass after the fix — verify the red state before implementing
    (matches this file's own TDD history, e.g. commit `cfc2707`).
  - **FR-2:** no new test — the four existing override-reattach tests must pass unmodified against the new
    code path. Treat any edit to them as a signal something is wrong with the fix, not the tests.
  - **FR-3:** attach override → commit locally (nothing pushed) → advance `origin/<branch>` independently
    via a second clone (copy the exact technique already used in
    `test_attach_reattach_with_origin_after_server_side_advance`) → reattach → assert
    `pytest.raises(GitError)` → assert neither the worktree's `HEAD` nor `origin/<branch>` moved as a result
    of the reattach call.
- **CLAUDE.md invariant 31:** apply the design's proposed replacement wording verbatim (design-01.md §4) as
  part of the same change set — not a follow-up commit. A stale invariant describing the pre-fix behavior
  is worse than no invariant at all, since a future session will trust it over reading the code.
- **Sequencing within `development`:** (1) sync branch onto `origin/main` — mandatory, see Prerequisites;
  (2) write FR-4's test, confirm red; (3) implement `_is_ancestor`/`_reconcile_override_reattach`; (4) write
  FR-3's test; (5) run the full suite, confirming FR-2's four tests are untouched-and-green plus
  `test_mergeability_e2e.py` and `test_resolve_conflict_behavior.py` (fake-driven, unaffected by a real-git
  change but worth confirming nothing else implicitly depended on the old reset-always behavior); (6) the
  CLAUDE.md edit; (7) land, `Closes #86`.

## 4. Open questions — resolved

- **Q1 (extend `MemoryWorkspace` to model reset-on-reattach): defer, don't build it in this change.** The
  real-git test in `tests/test_git_workspace.py` is the authoritative, already-precedented way this repo
  regression-tests exactly this class of driver bug (see CLAUDE.md's own explanation of why
  `test_smoke.py`/`test_smoke_git.py` stay real-FS/real-git). Teaching the fake to track a
  `pushed_head`/`local_head` split is a genuine future improvement for faster workflow-level e2e coverage,
  but it's new scope with its own design questions (does every fake workspace method need the split, or
  just override reattach?) that don't belong bundled into a bug fix. Revisit only if a *future* bug in this
  family turns out to need workflow-level (not driver-level) coverage to catch.
- **Q2 (auto-recover the diverged case instead of failing): don't — raise is correct and final for this
  change.** Auto-recovery would mean `attach()` unilaterally choosing to discard either a local commit or a
  server-side one — exactly the silent-data-loss failure mode this whole fix exists to close. The plan's
  instinct to defer this to a future watchdog is right: a watchdog can inspect *both* the local reflog and
  the PR's current state before deciding, which `attach()` — called mid-flight inside a behavior, with no
  view of the broader task history — structurally cannot do safely. Don't build speculative recovery logic
  for a race the plan itself notes is rare.
- **Q3 (invariant 31 wording): adopt the design's wording as-is.** It correctly scopes the change to the
  reattach half of the sentence, leaves the create-path reconciliation (invariant 30, unaffected) alone, and
  ends with an explicit anti-regression note ("Don't collapse this back to an unconditional reset — that's
  the bug `#86` fixed") — valuable given this exact invariant was already loosened once before for a
  different reason (commit `06cfacb`/`cfc2707`) and could plausibly be "simplified" again by someone who
  only sees the reattach-a-stale-branch case.

## 5. Risks and mitigations

- **Risk — this task's own worktree is stale relative to `origin/main` (69 commits behind at last fetch;
  was 65 when plan-01.md was written), and the resolver feature plus the exact buggy code don't exist on
  this branch at all yet** (confirmed: `git grep override` in this worktree's `git_workspace.py` returns
  nothing). Every line reference and quoted block in plan/design/this document is against `origin/main`, not
  this checkout. **Mitigation:** merging `origin/main` in first is not optional groundwork, it's a hard
  prerequisite the plan already calls out as step 1 — reiterated here so `development` doesn't skip it and
  then find the target code doesn't exist. I checked this merge is mechanically safe before signing off on
  the plan: `git merge-tree $(git merge-base HEAD origin/main) HEAD origin/main` produces zero `<<<<<<<`
  conflict markers, and this branch has made no local edits to `CLAUDE.md` (only added two new files under
  `.artifacts/`), so the merge that will pull in invariant 31's current text is a clean, non-conflicting
  merge — not a risk requiring manual conflict resolution, just a required step.
- **Risk — a future edit "simplifies" the three-way check back toward an unconditional reset**, since the
  override-reattach reconciliation has already been loosened once for a different bug (the
  `06cfacb`/`cfc2707` history the plan cites). **Mitigation:** the CLAUDE.md invariant-31 replacement wording
  explicitly names this failure mode and cites `#86` — make sure that sentence survives verbatim in the
  landed commit, it's the guardrail against regressing this exact fix a second time.
- **Risk — `_is_ancestor`'s bare `subprocess.run` (no `cwd=`, no `-C`-adjacent `_git()` wrapper) silently
  swallows a *different* kind of git failure** (e.g. corrupted repo, permissions) as "not an ancestor" →
  falls into the `else: raise GitError("diverged")` branch with a misleading message, rather than surfacing
  the real error. This mirrors the existing `_branch_exists_locally` helper's same tradeoff, so it's
  consistent with the codebase's existing risk posture, not a new one introduced by this fix — noted so
  `development` doesn't feel obligated to invent new error handling here that the sibling helper doesn't
  have either. Not a blocker.
- **Non-risk, confirmed:** `clean -fd` running unconditionally on the "local ahead, preserve" branch cannot
  discard the resolver's merge commit — `clean` only ever removes *untracked* files, never touches commits
  or tracked-file state, so FR-1's AC1 (HEAD survives) and the safety of running `clean -fd` on every
  non-raising path are independent guarantees, not in tension.

## Prerequisites before implementation begins

1. Sync this task's branch onto `origin/main` (`git fetch origin && git merge origin/main`) — confirmed
   clean, zero conflicts, mandatory since the target code doesn't exist on this branch yet.
2. No other prerequisite. No new dependency, no schema/migration, no port change, no coordination with any
   other in-flight branch (`maxParallel` or otherwise) — this fix is fully self-contained inside one driver
   file plus its test file plus one CLAUDE.md paragraph.

Proceed to `development`.
