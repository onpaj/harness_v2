# Architecture assessment: mergeability watcher (auto-rebase + resolver)

Reviewed against `plan-01.md`, `design-01.md`, and the current source
(`app.py`, `projection.py`, `dispatcher.py`, `consumer.py`, `source_poller.py`,
`ports/{workspace,source,agent}.py`, `drivers/{git_workspace,github_client,
github_source,github_forge,memory,worktree_artifacts,fs_agents}.py`,
`behaviors/{agent,landing}.py`, `models.py`, `cli.py`, `tests/test_architecture.py`).

Verdict: **the shape is right, but there is one load-bearing bug in
design-01.md's `GitWorkspace.attach` snippet that would fail every dirty-PR
resolver task in real use.** Everything else — the new `TaskSource` driver,
the `resolve → land` workflow, the `WorkspaceHandle.merge` port method, the
`build()`/`BoardProjection` multi-workflow wiring — is approved as designed.
Section "Risks and mitigations" #1 below is the one thing that must change
before implementation starts; everything else is confirmation plus a few
implementation-guidance refinements.

## Alignment with existing patterns and invariants

I traced every touched invariant against the current source, not just the
design doc's prose claims:

- **Invariant 1/17** (drivers wired only from `app.py`/`cli.py`, ports don't
  leak). `test_architecture.py::test_only_app_and_cli_wire_drivers` and
  `test_behaviors_import_only_ports_not_drivers` are real, enforced tests —
  confirmed `ResolveConflictBehavior` in `behaviors/resolve_conflict.py` may
  import `ports.workspace`, `ports.agent`, `ports.clock`, `ports.events`, and
  `behaviors.agent.compose_prompt` (same package, not a driver), but nothing
  from `drivers/`. Design-01.md's snippet already respects this.
- **Invariant 2/3** (dispatcher routes, consumer delivers, behavior decides).
  Confirmed by reading `dispatcher.py` and `consumer.py`: `Dispatcher.tick()`
  resolves `self._workflows.get(task.workflow_template)` **per task**
  (`dispatcher.py:60`), so a task carrying `"resolver"` already routes
  correctly with **zero** dispatcher changes — design-01.md's claim here is
  correct, verified against the actual code, not assumed.
- **Invariant 9** (worker commits, not the agent/consumer). Confirmed:
  `ResolveConflictBehavior.run()` calls `handle.commit(...)` itself in both
  branches (clean-merge and post-agent), matching `ClaudeCliBehavior`'s
  pattern exactly (`behaviors/agent.py:79`).
- **Invariant 15** (`task.repository` is a name, not a path). The branch
  override (`task.data["branch"]`) is orthogonal to this — it only changes
  which ref is checked out, never how the repo root is resolved. Confirmed:
  `GitWorkspace.attach` still calls `self._registry.resolve(task.repository)`
  unconditionally in both the design's snippet and the current code.
- **Invariant 21** (outward projection idempotent, doesn't block decisions).
  `GithubMergeabilityWatcher.poll()`'s per-PR `try/except` around
  `update_branch` mirrors `SourcePoller.tick`'s existing top-level
  `try/except` (`source_poller.py:59-65`) — necessary because `poll()` itself
  now does I/O with side effects beyond returning tasks, addressed below.
- **`test_architecture.py` guard I want to flag explicitly for the dev step**:
  `test_orchestration_does_not_import_work_ports` blocks `dispatcher.py`/
  `consumer.py` from importing `ports.workspace`/`ports.agent`/etc. Nothing in
  this design asks them to — noted only so the dev step doesn't "helpfully"
  add a workflow-name check in `dispatcher.py` for the new resolver flow. It
  needs none: the dispatcher is already workflow-agnostic.

No invariant is violated. This is a horizontal extension (new driver + new
behavior + wiring), not a change to any core decision-making path — consistent
with how phase 4's `TaskSource` itself was added.

## Proposed architecture: confirmed, one correction, two refinements

**Confirmed as designed:**
- `PullRequestInfo` + `list_pull_requests`/`update_branch` on `GithubClient`
  (both `FakeGithubClient` and `HttpGithubClient`). The two-tier
  list-then-per-PR-detail fetch for `mergeable_state` is correct — I confirmed
  against the current `HttpGithubClient.list_issues`/`find_pull_request`
  (`drivers/github_client.py:161-220`) that the file's only HTTP primitives are
  `urllib.request`/`json`, so the new methods fit the file's existing shape and
  add no dependency.
- `GithubMergeabilityWatcher(TaskSource)` widening `poll()`'s contract to also
  perform one outward side effect (`update_branch` on `behind`) per tick. This
  is a real widening of the `TaskSource.poll()` contract (`ports/source.py:66`
  currently documents it as "bring in new tasks," full stop) — accept it, but
  make it textual: add one sentence to the `poll()` docstring in
  `ports/source.py` noting that an implementation *may* perform an idempotent,
  side-effecting action per polled item that produces no task (precedent:
  `GithubTaskSource.poll()` already does something adjacent — it swaps a label
  as part of claiming — so this isn't unprecedented, just newly explicit). This
  keeps the port's contract honest for the next implementation rather than
  silently accepting scope creep through one driver.
- `LandingBehavior` reused unmodified. Confirmed via `github_forge.py:105`
  (`find_pull_request` matches on `head=owner:branch`) that landing a resolver
  task's fix onto the *same* branch resolves idempotently to the existing PR —
  no duplicate PR, no forge change needed.
- `column_order`/`BoardProjection` accepting `*extra_workflows`. Confirmed the
  bug design-01.md diagnoses is real: `BoardProjection._store` (`projection.py:
  119-122`) silently drops any column not in `self._order`, and `self._order`
  is built from exactly one `Workflow` (`projection.py:51`). Without this fix
  every resolver task would vanish from the board the moment it entered
  `resolve` — invisible, not merely mis-columned. Correctly caught, correctly
  fixed by threading `extra_workflows` through `BoardProjection.__init__` and
  `build()`.
- `ResolveConflictBehavior` as a dedicated class over a flag on
  `ClaudeCliBehavior`. Agreed, for the reason design-01.md gives (invariant 14
  keeps persona-as-data separate from control-flow-as-code) — a `merge()` call
  before the agent-or-not fork is exactly the kind of thing that doesn't
  belong as a branch in the generic behavior.

**Correction — this is the one blocking issue (full detail in Risks #1):**
design-01.md's `GitWorkspace.attach` override snippet does
`git worktree add <path> -B <branch> origin/<branch>` with no `--force`. Every
harness-authored PR's branch is, by construction, still checked out in the
*original* task's own worktree directory (nothing in this codebase ever
removes a worktree — confirmed by grep, zero hits for `worktree remove` /
`rmtree` / `prune` anywhere under `src/harness/`). Git refuses to check out a
branch that's already checked out elsewhere, so this command fails with a
`GitError` on the *first* resolver task ever run, unconditionally. The fix is
a one-flag change: `git worktree add --force <path> -B <branch>
origin/<branch>`. See Risks #1 for why `--force` is the correct fix (not a
worktree-reuse or worktree-cleanup redesign) and why those alternatives are
worse.

**Refinement 1 — reattach-without-fetch on the override path.** design-01.md's
snippet only fetches+checks-out on first attach; a reattach (existing
worktree at `<worktrees_root>/<resolver-task-id>`, e.g. after a harness
restart mid-`resolve`) just does `reset --hard HEAD` + `clean -fd` against
whatever the local branch already points to — it does not re-fetch. This is
**not a new problem** — it's byte-for-byte the same limitation the
non-override path already has today — so it needs no special handling, just
confirmation that resolver tasks inherit an existing, accepted sharp edge
rather than a new one. Don't "fix" this as part of this feature; it's out of
scope and orthogonal (see plan-01.md's non-goals).

**Refinement 2 — `ports/source.py` docstring.** Covered above under
"Confirmed" — a one-line addition, not a design change.

## Implementation guidance

**File-by-file, in the plan's step order (plan-01.md's rough plan is sound,
follow it as-is except where noted):**

1. `drivers/github_client.py` — add `PullRequestInfo`, `list_pull_requests`,
   `update_branch` to `GithubClient` (ABC), `FakeGithubClient`,
   `HttpGithubClient`, exactly as design-01.md specifies. `FakeGithubClient`
   needs its own `PullRequestInfo` store (`add_pull_request`) plus
   `updated_branches` audit list — keep it separate from `pulls:
   list[PullRequestRef]` (owned by the forge's `find_pull_request`/
   `create_pull_request`), don't try to unify the two shapes; they answer
   different questions (`PullRequestRef` has no `mergeable_state`).

2. `drivers/git_workspace.py` — `GitWorkspace.attach` branch override, **with
   `--force`** on the `worktree add` call (the fix above). Everything else
   in design-01.md's snippet is correct: `override = task.data.get("branch")`,
   fetch-then-add on first attach, reset-and-clean on reattach unchanged.

3. `ports/workspace.py` — add `WorkspaceHandle.merge(base: str) -> bool`
   exactly as specified. `GitWorkspaceHandle.merge` implementation as
   designed (`git fetch` + `git merge --no-commit --no-ff`, `CONFLICT`/
   `Automatic merge failed` in stdout → `True`, clean exit → `False`, anything
   else → raise `GitError`). Confirmed this composes correctly with the
   existing `commit()`: `git commit` during an in-progress merge
   (`MERGE_HEAD` present) automatically produces a two-parent merge commit —
   no `--continue`/special flag needed, `commit()`'s existing `add -A` +
   `status --porcelain` + `commit -m` sequence (`git_workspace.py:78-87`)
   works unmodified for this case.
   `drivers/memory.py`'s `MemoryWorkspaceHandle` gets the settable
   `conflicted: bool` + `merges: list[str]` seam as designed — this is the
   only handle `ResolveConflictBehavior`'s unit tests should touch; don't
   reach for `GitWorkspaceHandle` in unit tests (that's what
   `test_smoke_git.py`-style coverage is for).

4. `workflows/resolver.json` (written by `harness init`, constant
   `RESOLVER_DEFINITION` in `cli.py` next to `DEFAULT_DEFINITION`) +
   `behaviors/resolve_conflict.py` (`ResolveConflictBehavior`, as specified) +
   a `resolve` persona following the same carried-over-from-v1 convention as
   `AGENT_PERSONAS` (`cli.py:243-252`) — add `"resolve"` as a new key there,
   not a one-off inline string, so `_write_default_agents` picks it up for
   free through the existing generic path.
   Test file: `tests/test_resolve_conflict_behavior.py`, mirroring
   `tests/test_landing_behavior.py`'s structure (drive it with
   `MemoryWorkspace`/`FakeAgentRunner`, no real git).

5. `drivers/mergeability_watcher.py` (`GithubMergeabilityWatcher`) as
   specified. Test file: `tests/test_mergeability_watcher.py`, against
   `FakeGithubClient`, following `tests/test_github_source.py`'s shape
   (construct source, call `.poll()`, assert on returned tasks and on the
   fake client's audit state).

6. Wiring:
   - `app.py`: `column_order(workflow, *extra)` + `BoardProjection.__init__(...,
     extra_workflows=())`; `build(..., extra_workflow_names=())` unions
     `step_queues` across `workflow.steps()` and each extra workflow's
     `steps()`; `behavior_for` grows the `RESOLVE_STEP` branch before the
     generic `catalog is not None` case. Test file: extend
     `tests/test_projection.py` for the `column_order` multi-workflow case,
     and add a `build()`-level test (new or extend an existing `app.py` test)
     asserting a task on `"resolver"` gets a working `resolve` queue/consumer.
   - `cli.py`: `_mergeability_sources(...)` mirroring `_github_sources`
     verbatim in structure (same "no token → `[]`" default, same
     one-per-registry-entry loop); new `run` flags
     `--watch-mergeability`/`--no-watch-mergeability`
     (`argparse.BooleanOptionalAction`) and `--resolver-workflow`; `_run()`
     appends the mergeability sources to `_github_sources(...)`'s list and
     passes `extra_workflow_names` to `build()` when any are present; `_init()`
     also writes `workflows/resolver.json` (guarded the same way
     `DEFAULT_DEFINITION` is — `if not definition_path.exists()`) and calls
     `_write_default_agents(layout, resolver_workflow)` a second time (already
     generic — no change needed to that function itself, confirmed by reading
     `cli.py:284-301`).
   - **One correction to design-01.md's cli.py section**: it references a
     `--github-repo` flag that does not exist in the current CLI (`cli.py`'s
     actual mechanism is: `GITHUB_TOKEN` env var presence + entries in
     `repos.json` — there is no per-repo opt-in flag). Functionally the
     design's description ("defaults off without a token") still holds; just
     don't introduce a `--github-repo` flag that doesn't already exist as
     part of this feature. `--watch-mergeability` should follow the same
     token-gated-by-default pattern `_github_sources` already uses, not
     invent a new opt-in mechanism.

7. Docs: extend `CLAUDE.md`'s invariants list per plan-01.md's open question —
   add the two new invariants it names ("a task's workspace branch is
   `harness/<task.id>` unless `data.branch` overrides it" and "conflict
   resolution is always a merge, never a rebase") **plus a third**, given the
   `--force` finding above: **"a task's worktree directory is never removed;
   a second worktree may force-checkout an already-checked-out task branch
   (`git worktree add --force`) when resolving that PR's conflicts — this is
   deliberate, not a bug to fix."** Future readers of `git_workspace.py` need
   this stated, or someone will "fix" the `--force` away.

**Data flow** (confirmed unchanged from design-01.md): `SourcePoller` → inbox
→ dispatcher (routes by `task.workflow_template`) → `resolve` queue → Consumer
→ `ResolveConflictBehavior` (attach existing branch into a *second* worktree,
merge, agent-if-conflicted, commit) → inbox → dispatcher → `land` queue →
Consumer → `LandingBehavior` (unchanged: push + idempotent PR open) → inbox →
dispatcher → `end`/`done`.

## Risks and mitigations

1. **Confirmed blocking risk: `git worktree add` without `--force` fails on
   every resolver task.** Root cause, verified by reading the whole
   `src/harness` tree: no code path ever removes a worktree
   (`git worktree remove`/`rmtree`/`prune` — zero matches). A harness-authored
   PR's branch (`harness/<task-id>`) is therefore *always* still checked out
   in `<worktrees_root>/<task-id>`, the directory the original task left
   behind. `GitWorkspace.attach`'s branch-override path, as specified in
   design-01.md, tries to check out that same branch into a **new** directory
   (`<worktrees_root>/<resolver-task-id>`) — git refuses this unconditionally
   (a branch may be checked out in at most one worktree) unless `--force` is
   passed. Without the fix, `resolve` fails on its very first git command,
   every single time, for every dirty PR.
   - **Why not "reuse the original task's worktree directory" instead of
     forcing a second one?** Because `WorktreeArtifactView._dir()`
     (`drivers/worktree_artifacts.py:23-24`) hardcodes
     `worktrees_root / task_id` — it takes only a `task_id` (per the
     `ArtifactView` port signature used by `api/routes.py`), never a full
     `Task`, and never consults a `task.worktree` field. Reusing the original
     directory for the resolver task would silently break the board's ability
     to show the resolver task's own `resolve-01.md` artifact (it would look
     under `<worktrees_root>/<resolver-id>/.artifacts/<resolver-id>/`, which
     wouldn't exist) — trading one bug for another, and this one violates
     FR-7 (operator visibility) instead of just failing loudly.
   - **Why not clean up the original task's worktree first?** Same reason,
     worse: it would delete the *original* task's own historical artifacts
     out from under the board, breaking visibility for a task that already
     finished successfully — a strictly worse trade.
   - **Mitigation: `--force` on the `worktree add` call.** This is git's own,
     documented escape hatch for exactly this situation ("branch already
     checked out by another working tree"). It's safe here specifically
     because the original worktree is permanently inert — no consumer ever
     revisits a task once it reaches `end`/`done`, so nothing will ever write
     to that original checkout again; two worktrees sharing a branch ref is
     only dangerous when both are live. Add one test to
     `tests/test_git_workspace.py`: attach a task, land it (simulating the
     branch existing in a first worktree), then attach a **second**, distinct
     task with `data["branch"]` set to the first task's branch, and assert it
     succeeds instead of raising `GitError`. Without this test the bug would
     be invisible in unit tests (`MemoryWorkspace`/`MemoryWorkspaceHandle`
     don't touch real git) and would only surface in `test_smoke_git.py` or,
     worse, in production against a real GitHub PR.

2. **Secondary risk (non-blocking): worktree accumulation.** Every resolver
   task adds one more worktree directory on a branch that already had one
   (or, for a PR that goes dirty a second time, two). Nothing prunes these —
   consistent with the harness's existing behavior (no task's worktree is
   ever cleaned up today either), so this feature doesn't introduce a new
   category of problem, only makes an existing one accrue faster on
   frequently-conflicting PRs. Not a blocker; worth a one-line note in the new
   CLAUDE.md invariant (see Implementation guidance #7) so it reads as
   deliberate, not overlooked. Out of scope to fix here — a worktree-GC
   feature is its own task, mirroring plan-01.md's existing "webhooks" /
   "auto-merge" out-of-scope items.

3. **Risk: `poll()`'s widened contract (task source + side effect) is easy to
   miscopy into a future `TaskSource` that shouldn't have one.** Mitigated by
   the one-sentence port docstring addition (see "Proposed architecture" —
   Confirmed). Low severity; purely a documentation debt risk, not a runtime
   one, since only this one driver exercises it.

4. **No risk to the core decision-making path.** The dispatcher needs zero
   code changes (confirmed by reading `dispatcher.py` — it already resolves
   the workflow per task) and the consumer needs zero code changes — the
   entire blast radius of a bug in `ResolveConflictBehavior` is "that task
   fails into `failed/`" (via `consumer.py`'s existing `except Exception`
   guard, invariant 3), never a mis-route of an unrelated task. This
   containment is worth stating plainly, the same way it was for the prior
   full-screen-tab-view feature: this is a horizontal, driver-level addition,
   not a change to the orchestration core.

## Prerequisites before implementation begins

One prerequisite, not optional: **design-01.md's `GitWorkspace.attach`
snippet must be implemented with `--force` on the `worktree add` call for the
override path** (Risks #1). This isn't a nice-to-have refinement — without
it, step 2 of the plan produces code that fails 100% of real resolver runs,
and every following step (3-7) would be built and tested against a git
operation that doesn't actually work outside of `MemoryWorkspace`-backed unit
tests. The dev step should treat this as a correction to design-01.md, not an
open question — it's already resolved above with a concrete flag and a
concrete test to add.

All three of plan-01.md's other open questions are already resolved by
design-01.md and confirmed correct by this review, with no changes:
merge-then-maybe-agent lives in a dedicated `ResolveConflictBehavior` (agreed);
`resolver` stays `resolve → land` for v1, no review step (agreed, mechanical
enough); non-`behind`/`dirty` states are left alone for v1 (agreed, matches
FR-6's isolation stance); per-repo opt-out stays a single CLI flag, not
per-`repos.json`-entry config (agreed, mirrors the existing
`GITHUB_TOKEN`-gated all-or-nothing pattern).
