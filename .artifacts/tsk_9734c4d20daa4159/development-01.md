# Development — mergeability watcher (auto-rebase "behind" PRs, queue conflicts to a resolver step)

Implemented per `plan-01.md` / `design-01.md`, following `architecture-01.md`'s
guidance and its one required correction (below).

## What was implemented

### 1. `GithubClient` additions (`src/harness/drivers/github_client.py`)
- `PullRequestInfo` dataclass (`number`, `url`, `head_branch`, `head_sha`,
  `base_branch`, `mergeable_state`) — kept as a separate store from
  `PullRequestRef` (owned by the forge's find/create pair), since they answer
  different questions.
- `GithubClient.list_pull_requests(repo, *, head_prefix=None)` and
  `update_branch(repo, number)` (idempotent — a 422 is swallowed), added to the
  ABC, `FakeGithubClient` (with `add_pull_request`/`updated_branches` test
  seams; `update_branch` also flips the fake's stored state to `"clean"` so
  e2e tests can drive both branches without touching real git), and
  `HttpGithubClient` (two-tier: one list call + one per-PR detail call, since
  GitHub's list endpoint doesn't return `mergeable_state`).

### 2. `GitWorkspace.attach` branch override (`src/harness/drivers/git_workspace.py`)
- `task.data["branch"]`, when set, checks out that *existing* branch instead
  of creating `harness/<task.id>` from HEAD. The absent-key path (every
  non-resolver task) is unchanged.
- **Correction to architecture-01.md's fix**: the doc's assessment said
  `--force` alone fixes the design's `-B branch origin/branch` snippet. I
  verified empirically (real `git worktree add`, see below) that this is
  wrong — git refuses to *force-reset* (`-B`) a branch that is checked out in
  another worktree, regardless of how many times `--force` is passed; that is
  a separate guard from the "already checked out" guard `--force` actually
  lifts. The working fix: reuse the already-existing local branch as-is with a
  plain (non-`-B`) `--force`d checkout — the common case, since the original
  task's worktree created that local ref — falling back to `-b branch
  origin/branch` (a genuinely fresh local branch, which needs no force) only
  the one time no local copy exists yet. Verified with a real git repo before
  writing the fix:
  ```
  $ git worktree add --force ../wt2 feature        # OK — reuses existing branch
  $ git worktree add --force ../wt3 -B feature origin/feature
  fatal: cannot force update the branch 'feature' checked out at '...'
  ```
  `_branch_exists_locally()` (a `git rev-parse --verify --quiet` check) picks
  the path. This is documented in the module docstring and in new
  `CLAUDE.md` invariant #26.

### 3. `WorkspaceHandle.merge()` port method
- `ports/workspace.py`: `merge(base: str) -> bool` — True means conflict
  markers are now in the tree, False means the merge applied cleanly and is
  staged.
- `GitWorkspaceHandle.merge`: `git fetch origin <base>` +
  `git merge --no-commit --no-ff origin/<base>`; `CONFLICT`/"Automatic merge
  failed" in stdout → `True`; clean exit → `False`; anything else → `GitError`.
  Confirmed `commit()`'s existing add-all+commit sequence produces the
  two-parent merge commit unmodified once conflicts are resolved (no
  `--continue` needed).
- `MemoryWorkspaceHandle` gets the `conflicted`/`merges` test seam, and
  `MemoryWorkspace.attach` now honors `task.data["branch"]` too (so
  unit tests can assert on the branch a resolver task attaches).

### 4. `ResolveConflictBehavior` (`src/harness/behaviors/resolve_conflict.py`, new)
- Attaches the workspace, merges `task.data["source"]["base"]`. Clean merge →
  commits `"[resolve] merge <base> — no conflicts"` and returns `done`
  without spending an agent call (FR-4). Real conflict → runs the `resolve`
  persona through the same `AgentRunner`/artifact-attempt machinery
  `ClaudeCliBehavior` uses (`compose_prompt`, `next_attempt`), then the worker
  commits the agent's summary (invariant 9).
- `workflows/resolver.json` (`resolve → land → end`) and the default
  `resolve` persona are new constants in `cli.py` (`RESOLVER_DEFINITION`,
  `_RESOLVE_PERSONA` in `AGENT_PERSONAS`), written by `harness init`.

### 5. `GithubMergeabilityWatcher` (`src/harness/drivers/mergeability_watcher.py`, new)
- `TaskSource` with `kind="mergeability"`. `poll()`: `behind` → `update_branch`
  (side effect, no task, per-PR `try/except` so one bad PR doesn't block the
  rest of the tick); `dirty` → a new resolver `Task` with
  `dedup_key="mergeability:<repo>:<pr>:<head_sha>"` (so a PR that goes dirty,
  gets fixed, then goes dirty again on a new commit produces a fresh task);
  anything else → left alone (v1 scope). `report_progress`/`finish` project a
  `harness:resolving` label onto the PR while in `resolve`/`land`, cleared on
  finish.
- `ports/source.py`'s `TaskSource.poll()` docstring now documents this
  widened contract (an implementation may perform one idempotent side effect
  per polled item), per architecture-01.md's guidance.

### 6. Wiring
- `projection.py`: `column_order(workflow, *extra)` and
  `BoardProjection(workflow, extra_workflows=())` fold in a second workflow's
  steps so a resolver task's `resolve` column actually renders (this was the
  bug architecture-01.md flagged — without it, resolver tasks would silently
  vanish from the board).
- `app.py`: `build(..., extra_workflow_names=())` unions step queues across
  the primary and every extra workflow; `RESOLVE_STEP = "resolve"` constant;
  `behavior_for` grows a branch assigning `ResolveConflictBehavior` before the
  generic `ClaudeCliBehavior` case (mirrors how `landing_step` is handled).
  Dispatcher/consumer needed **zero** changes — confirmed (again) that the
  dispatcher already resolves the workflow per task.
- `cli.py`: `_mergeability_sources(...)` mirrors `_github_sources` exactly
  (same token-gated-by-default pattern, one watcher per registered repo with
  a GitHub origin); new `run` flags `--watch-mergeability`/
  `--no-watch-mergeability` (default on) and `--resolver-workflow` (default
  `resolver`); `_run()` appends mergeability sources and passes
  `extra_workflow_names` to `build()` when any exist; `_init()` also writes
  `workflows/resolver.json` (idempotently, same guard style as the default
  workflow) and calls the existing, already-generic `_write_default_agents`
  a second time for the resolver workflow — no changes needed to that
  function itself.

### 7. Docs
- `CLAUDE.md`: three new invariants (#24 branch override, #25
  merge-not-rebase, #26 worktree-never-removed / `--force` reasoning) and the
  module map now lists `mergeability_watcher` and `resolve_conflict`.

## Files created
- `src/harness/behaviors/resolve_conflict.py`
- `src/harness/drivers/mergeability_watcher.py`
- `tests/test_resolve_conflict_behavior.py`
- `tests/test_mergeability_watcher.py`
- `tests/test_mergeability_e2e.py`

## Files changed
- `src/harness/drivers/github_client.py`, `drivers/git_workspace.py`,
  `drivers/memory.py`, `ports/workspace.py`, `ports/source.py`,
  `projection.py`, `app.py`, `cli.py`
- `tests/test_github_client.py`, `test_git_workspace.py`,
  `test_workspace_memory.py`, `test_agent_behavior.py` (local `WorkspaceHandle`
  test double needed the new abstract `merge()` method), `test_projection.py`,
  `test_app.py`, `test_cli.py`
- `CLAUDE.md`

## How to verify
```sh
.venv/bin/pytest -q
```
517 passed, 1 skipped (`test_smoke_claude.py`, opt-in real-`claude` smoke —
unaffected by this change). This includes the real-git smoke suites
(`test_smoke_git.py`, `test_smoke.py`, `test_smoke_github.py`) and
`test_architecture.py`'s import-boundary guards, all green.

Manual check of the new `harness init` output:
```sh
harness init --root /tmp/x
cat /tmp/x/workflows/resolver.json   # resolve -> land -> end
cat /tmp/x/agents/resolve.json       # the merge-conflict persona
ls /tmp/x/agents/                    # no land.json — landing has no persona, unchanged
```

Notable test additions beyond straight unit coverage:
- `tests/test_git_workspace.py`: a **real** git test proving the branch
  override force-checks-out a branch already checked out by another worktree
  (the scenario every resolver task hits in production), plus the
  create-from-origin fallback path and a real merge-conflict test asserting
  actual `<<<<<<<`/`>>>>>>>` markers land in the tree.
- `tests/test_mergeability_e2e.py`: dirty PR → resolver → same PR (not a
  duplicate); behind PR → auto-update, no task; a simulated restart re-seeding
  a fresh `SourcePoller` from disk does not re-queue the same conflict.

## Deviations from the architecture doc
Only one, already covered above: the `-B ... --force` fix architecture-01.md
prescribed does not actually work (verified against real git); the correct
fix reuses the existing local branch (or creates it fresh from
`origin/<branch>` when absent), `--force`d only for the checkout-elsewhere
case `--force` actually covers. Everything else in the design/architecture was
implemented as specified with no other deviations.
