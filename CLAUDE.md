# harness_v2 — orientation for Claude

An orchestration harness for multiple agents. The unit of work is a **task**, which
travels between queues according to a **workflow** — a small state machine with
explicit edges.

> **Project language is English — always.** All code, comments, docstrings,
> string literals, tests, docs, commit messages and PRs are written in English.
> Do not introduce any other language, even when the prompt or an issue is in one.

Phase 1 spec: `docs/superpowers/specs/2026-07-19-orchestration-phase1-design.md`
Phase 1 plan: `docs/superpowers/plans/2026-07-19-orchestration-phase1.md`
Phase 2 spec: `docs/superpowers/specs/2026-07-20-orchestration-phase2-design.md`
Phase 2 plan: `docs/superpowers/plans/2026-07-20-orchestration-phase2.md`
Phase 3 spec: `docs/superpowers/specs/2026-07-20-orchestration-phase3-design.md`
Phase 3 plan: `docs/superpowers/plans/2026-07-20-orchestration-phase3.md`
Generic triggers spec: `docs/superpowers/specs/2026-07-22-generic-triggers-design.md`
Generic triggers plan: `docs/superpowers/plans/2026-07-22-generic-triggers.md`
Processes spec: `docs/superpowers/specs/2026-07-22-processes-design.md`
Processes plan: `docs/superpowers/plans/2026-07-22-processes.md`

The project is built **phase by phase**. Phase 1 is a POC of the orchestration loop.
Phase 2 adds **worktrees, artifacts and landing**: each phase works in a worktree,
writes artifacts, commits per phase and opens a PR at the end. Phase 3 swaps
`DummyBehavior` for a **real agent via `claude -p`**: a step's persona (`AgentSpec`)
is run by the shared `AgentRunner`, `task.repository` is a name (the path is
resolved by `RepositoryRegistry`), and artifacts move **into the worktree** under
`.artifacts/<id>/`, versioned. Real GitHub is still just a driver (forge) that gets
swapped out later.

## Invariants — do not break

1. **You may swap a driver, never its surroundings.** Every moving part sits behind
   a port in `ports/`. Neither `dispatcher.py` nor `consumer.py` may import anything
   from `drivers/` — wiring belongs exclusively in `app.py`. This is guarded by
   `tests/test_architecture.py`. See ADR-0001.
2. **Decision-making has three separate roles.** `ConsumerBehavior` says *what
   happened*, the dispatcher *where it goes next*, the consumer decides nothing.
   `consumer.py` must not contain a branch that depends on the outcome value; a test
   checks this by reading the source. See ADR-0002.
3. **The dispatcher changes status — with one exception.** The decision *where the
   task goes next* (step, `end`) belongs exclusively to the dispatcher. The one
   exception: when the consumer itself cannot deliver the task (the behavior raises
   an exception, or returns an invalid outcome), it writes the terminal status
   `failed` itself — symmetrically to how `Dispatcher._fail` does the same when
   routing fails. `lastOutcome` is written exclusively by the consumer.
4. **The router is a pure function.** `route()` must not touch I/O, time or state. See ADR-0004.
5. **Neither `api/` nor `projection.py` imports `drivers/`.** The UI must not know what the harness runs on. See ADR-0005.
6. **In `Harness.run()`, `recover()` comes before `hydrate()`.** The other way round loses tasks from `.processing/`. See ADR-0003.
7. **A task-movement event carries both `task` and `queue`.** Without it the projection won't see tasks created after startup.
8. **`repository`/`worktree` is read only by the behavior.** The router and dispatcher decide solely on `(status, lastOutcome)`, and — since workflows became optional — whether a workflow is present at all; never on `repository`/`worktree`, `step`, or `data`.
9. **The commit is done by the behavior driver, not the consumer and not the LLM.** The consumer knows no git. See ADR-0006.
10. **Artifacts are attempt-indexed** (`<task>/<step>/<attempt>/`). A step re-run never overwrites the previous attempt — otherwise the `request_changes` loop would vanish from the audit trail. See ADR-0006.
11. **`Workspace`/`Forge`/`ArtifactStore` are unknown to the dispatcher and consumer.** Only the behavior touches them; wiring in `app.py`. `api/` touches only `ArtifactView`. Guarded by `test_architecture.py`.
12. **Landing is a step, not magic.** It lands the artifacts into the worktree and opens a PR; it may fail into `failed/`. `end` stays a clean terminal. Phase 3: the artifacts are already in the worktree, landing doesn't copy them — it just opens a PR. See ADR-0009.
13. **The agent lives behind `AgentRunner`.** `ClaudeCliBehavior` knows nothing of subprocesses or CLI flags; a test drives it with `FakeAgentRunner`, the way phase 1 drove time with `FakeClock`. See ADR-0007.
14. **The persona is data, not code.** `behaviors/agent.py` has no branch on the agent's name — the difference between personas is the content of the `AgentSpec` supplied at construction. See ADR-0007.
15. **`task.repository` is a name, not a path.** Paths are resolved by `RepositoryRegistry` — machine-specific config (`repos.json`), outside the task. The worktree path is derived by the harness (`<worktrees_root>/<task_id>`). See ADR-0008.
16. **Artifacts live in the worktree under `.artifacts/<id>/`, versioned.** The agent writes them, the worker commits them. Attempt numbering (`<step>-NN`) is gapless across reset-on-reattach. See ADR-0006.
17. **`AgentRunner`/`AgentCatalog`/`RepositoryRegistry` are unknown to the dispatcher and consumer.** Only the behavior / wiring touches them. Guarded by `test_architecture.py`.
18. **The outside world of tasks is a single port `TaskSource` (`poll`/`report_progress`/`finish`).** GitHub is a driver; how the state is rendered (a label) is known only to the driver. See ADR-0010.
19. **A task's origin lives in `task.data.source`** (`{kind, repo, issue, url}`). The outward projection reads it from there, not from side state. Neither router nor dispatcher reads `data.source`. See ADR-0010.
20. **`TaskSource` is touched only by `SourcePoller` (core) and `SourceReflectorSink` (driver)**, wired in `app.py`. `dispatcher.py`/`consumer.py` don't import the port — guarded by `test_architecture.py`. See ADR-0010.
21. **The outward projection is idempotent and doesn't block decision-making.** `report_progress` twice is a no-op; a source failure is isolated by `CompositeEventSink` (and `SourcePoller.tick` catches the exception from `poll()`).
22. **`todo` is the board's name for the inbox's fresh tasks** (`status is None`) — the first column. It is a view concern only: the router and dispatcher never see a `todo` queue, and auto-flow is unchanged (a fresh task passes through `todo` into `start`).
23. **Operator control is a write-side port `TaskControl`, mirroring the read-side `BoardView`.** `restart` is a reset, not a routing decision: it clears `status`/`lastOutcome` and re-inboxes a `failed` task, then the dispatcher decides where next (invariant #3 holds). `TaskControl` is touched only by `TaskControlService` (core), `api/` and wiring — `dispatcher.py`/`consumer.py` don't import it; guarded by `test_architecture.py`. See ADR-0011 and ADR-0012.
24. **`failed/` has one reader — the healer; `healed/` is the never-consumed terminal.** This refines "terminal states are queues nobody consumes": `done`/`end`/`healed` are terminal, while `failed/` is drained by the `Healer` loop (an agent assigned to it) and by nothing else. The router and dispatcher never learn about `failed`/`healed` as steps. The healer is opt-in (`HealConfig`); with no healer wired, `failed/` stays a dead end exactly as before.
25. **The healer produces an issue, never a task, and never writes back to `failed/`.** A heal claims a task out of `failed/` exactly once and settles it to `healed/` — success *or* failure (agent error / `IssueError` become a `heal-failed` note, not a re-queue). So no failure is healed twice and nothing can loop; there is no recursion to guard.
26. **The healer's deliverable is opened by the worker loop, not the LLM.** The `healer` agent (persona as data) only drafts `issue.md` and returns a verdict; the `Healer` loop reads the draft and calls `IssueTracker.open_issue` (invariant 9). `IssueTracker` is a third port distinct from `Forge` (opens PRs) and `TaskSource.finish` (relabels), idempotent by a per-task marker.
27. **`IssueTracker` and the `Healer` loop are unknown to the dispatcher and consumer.** The healer is a core loop that imports only ports/models/ids (like `SourcePoller`); wiring lives in `app.py`. Guarded by `test_architecture.py`.
28. **A task's workspace branch is `harness/<task.id>` unless `task.data["branch"]` overrides it.** The override exists for exactly one case (the resolver workflow fixing an existing PR): `GitWorkspace.attach` checks out that *existing* branch instead of creating a fresh one from HEAD. Absent the key, every path is unchanged.
29. **Conflict resolution is always a merge, never a rebase.** `WorkspaceHandle.merge()` produces a two-parent merge commit, deliberately — a rebase would rewrite history on a branch that may already be pushed, breaking the no-force-push invariant `GitWorkspaceHandle.push()` relies on (a plain `push -u`, no `--force`).
30. **No task's worktree directory is ever removed.** Nothing under `src/harness` calls `git worktree remove`/`prune`. Consequence: a harness-authored branch is always still checked out in its original task's own worktree, so `GitWorkspace.attach`'s branch-override path force-checks it out into a *second* worktree (`git worktree add --force <path> <branch>`, reusing the existing local branch — not `-B`, which git refuses to force-reset on a branch checked out elsewhere no matter how many times `--force` is passed). Safe because the original worktree is permanently inert once its task reaches a terminal state. Deliberate — don't "fix" the `--force` away, and don't add worktree cleanup without re-checking this invariant.
31. **The branch-override's reused local ref is untrusted until reconciled with `origin`.** The shared `refs/heads/<branch>` only tracks `origin/<branch>` while every advance of the branch goes through a local commit+push in *some* worktree — `GithubMergeabilityWatcher.update_branch` breaks that by advancing the branch server-side with no local git touch at all. So immediately after the `--force`d `worktree add` in the reuse path, `GitWorkspace.attach` hard-resets the *new* worktree to `origin/<branch>`'s fetched tip before returning the handle. That reset targets the branch as checked out in the new worktree, not "elsewhere," so it isn't blocked by the guard invariant 30 relies on. Don't drop this reset — without it, a `behind`→`update_branch`→`dirty`→resolver sequence on the same PR leaves the resolver's worktree stale and its final `push` fails as non-fast-forward.
32. **`MergeChecker` is touched only by `MergeReconciler` (core) and wiring.** `dispatcher.py`/`consumer.py` don't import `ports.merge` — guarded by `test_architecture.py`, mirroring invariant 20's shape for `TaskSource`.
33. **`AgentAdmin`/`WorkflowAdmin`/`ProcessAdmin` are unknown to the dispatcher and consumer.** They are UI-facing admin ports, not orchestration ports — like `BoardView`/`TaskControl`, not like `AgentCatalog`/`WorkflowRepository`. `api/` touches only these admin ports; the filesystem drivers (`FilesystemAgentAdmin`, `FilesystemWorkflowAdmin`, `FilesystemProcessAdmin`) are wired exclusively in `cli.py`'s `serve()`. Guarded by `test_architecture.py`'s existing glob-based checks (no dedicated test needed).
34. **`IssueChecker` is touched only by `IssueReconciler` (core) and wiring.** `dispatcher.py`/`consumer.py` don't import `ports.issue_state` — guarded by `test_architecture.py`, mirroring invariant 32's shape for `MergeChecker`. The reconciler retires a stale task the same way merge/PR resolution already does — into `archived/` (invariant 24's terminal-queue shape, off every board column but gettable by id) — so "remove from the dashboard" adds no new board mechanism. See ADR-0013.
35. **A trigger produces tasks, never queue placements.** A `Trigger` (schedule- or condition-driven) hands a fresh `Task` to the inbox with a `workflow_template` **or** a `step`; the dispatcher alone places it (`route()`, invariants #3/#8). No trigger writes into a step queue. The "target any queue" capability is a workflow-less task carrying `step`, not a producer reaching past the dispatcher. See ADR-0014.
36. **A `Trigger` is a `TaskSource` that reflects nothing outward.** It implements only `poll()`; `report_progress`/`finish` are inherited no-ops. It stamps no `data.source`, so `SourceReflectorSink` lists it and ignores it — the same path that ignores a `harness submit` task. `TaskSource` stays whole (invariants #18–#20 unchanged); `Trigger` is a convenience base, not a new port.
37. **A scheduled trigger owns its cadence via the `Clock`, not via a loop.** `poll()` gates on the interval bucket `floor(now / interval)` and returns `[]` cheaply between fires; the shared `source_interval` is only polling granularity. The gate reads solely `Clock`, so it is `FakeClock`-testable and survives the poller's `sleep(0)` fast-path without re-firing.
38. **A scheduled trigger's `dedup_key` is bucket-keyed, giving at-most-once per interval across restarts.** The key includes `floor(now / interval)` (and, for `per-state` dedup, an observed `state_key` instead), so the existing `SourcePoller._seen` seeding makes one period yield one task even across a restart — the same exactly-once mechanism as GitHub ingestion (ADR-0010), with a non-constant key.
39. **A Process is a compile-time authoring aggregate, never a runtime object.** `processes/*.json` bundles a trigger (cadence), an action (a named `Check`), a target (workflow **or** step) and a `sink`, and `FilesystemProcessRepository` **compiles each into a `ScheduledTrigger`** that joins the existing `sources` list. Nothing under orchestration (`dispatcher`/`consumer`/`router`/`source_poller`) imports or names "process"; it is `cli.py` wiring turned into data. `build()` gains no parameter, and `triggers/*.json` is unchanged — a Process compiles to the *same* `ScheduledTrigger` a bare trigger file does, so the two surfaces coexist. Guarded by `test_architecture.py`. See ADR-0015.
40. **A Process's `sink` is a forward-compat seam: parsed, validated, `none`-only in v1.** The outbound reflection destination is a distinct field from the inbound origin so source ≠ destination stays open (GitHub in, Slack out) as a driver addition, never a schema change. v1 writes no `data.sink` and reflects nothing; a real sink would route via `SourceReflectorSink` on a destination identity that defaults to `source.kind`, and a stateful sink (create-then-update, e.g. a Slack message edited as progress advances) may return a persistable handle — refining invariant #21 from "no-op" to "convergent." No `Reflector` port is built until a real destination exists. See ADR-0015.

## Working here

```sh
.venv/bin/pytest -q
```

Python is **3.11** (`/Users/rem/.local/bin/python3.11`, a uv-managed CPython).
Development is a clone + `venv` + `pip install -e ".[dev]"`; **shipping** is
`uv tool install git+https://github.com/onpaj/harness_v2.git`, updated with
`harness update` (a thin wrapper over `uv tool upgrade harness`). `install.sh`
was retired in favour of that — don't reintroduce a second install path.

Unit and integration tests run on in-memory drivers and `FakeClock` — no disk
and no real waiting. Never write a test into them that sleeps in real time.

The deliberate exceptions are `tests/test_smoke.py` (real FS) and
`tests/test_smoke_git.py` (phase 3: real git worktree + artifacts in the worktree +
fake forge, the step's work driven by `ClaudeCliBehavior` with a local test-runner,
not real `claude`). Both crawl along on a real `asyncio.sleep(0.01)` — it's the only
coverage of the real FS/git drivers live. Don't tidy them into an in-memory shape;
that coverage would vanish.

`tests/test_smoke_claude.py` is an **opt-in** smoke with real `claude -p` — it runs
only with `HARNESS_SMOKE_CLAUDE=1`, otherwise it's skipped and `pytest -q` won't run it.
It covers the thin subprocess shell of `ClaudeCliRunner` that the fake runners bypass.

## Git conventions

**Commit straight into `main`.** In this phase that's the intended approach — don't
create a branch, don't open a PR, and don't ask. This applies to the harness's own repo.

**Commit messages are conventional commits — this is now load-bearing.**
`.github/workflows/release.yml` runs python-semantic-release on every push to
`main`: `feat:` bumps the minor, `fix:`/`perf:` the patch, `BREAKING CHANGE:` the
major, and everything else (`docs:`, `chore:`, `test:`, `refactor:`, `ci:`) only
shows up in the notes. A sloppy subject line silently means no release. The
release job pushes back a `chore(release): X.Y.Z` commit carrying `[skip ci]`;
that commit has no feat/fix, so it cannot trigger a release of its own.

**`main` requires the `test` status check to merge — a repo setting, not
workflow code.** Branch protection on `main` (GitHub Settings → Branches, or
`GET/PUT /repos/onpaj/harness_v2/branches/main/protection`) requires the
`test` check produced by `.github/workflows/ci.yml`'s `test` job before a PR
can merge. This is invisible in `git diff` — renaming or removing the `test`
job in `ci.yml` silently breaks the protection rule (GitHub then waits
forever on a check that never posts again, locking every merge button), so
any rename must be paired with updating the required check in branch
protection.

## Module map

Dependencies flow strictly downward, no cycles.

| Layer | Modules |
|---|---|
| Base | `models` (imports nothing from the package), `ids` |
| Logic | `router` (knows only `models`) |
| Base (package-free) | `models`, `ids`, `artifacts_layout` (the `.artifacts/<id>/<step>-NN` convention) |
| Ports | `ports/{queue,workflows,strategy,behavior,events,clock,workspace,artifacts,forge,board,agent,repos,source,control,logs,issues,merge,issue_state,triggers,updater,process_admin}` |
| Orchestration | `dispatcher`, `consumer`, `source_poller`, `task_control`, `healer`, `pr_watcher`, `merge_reconciler`, `issue_reconciler` — know only ports (and, for `pr_watcher`/`merge_reconciler`/`issue_reconciler`, the base `ids` module — not `workspace`/`forge`/`artifacts`/`agent`/`repos`/`drivers`) |
| Behaviors | `behaviors/{landing,agent,resolve_conflict}` — touch ports, not drivers |
| Drivers | `drivers/{fs_queue,fs_workflows,fifo_strategy,dummy_behavior,stdout_events,system_clock,memory,fs_artifacts,git_workspace,fake_forge,claude_cli,fs_agents,fs_repos,worktree_artifacts,source_reflector,github_client,github_source,github_forge,github_issues,github_issues_check,github_merge_checker,github_issue_checker,mergeability_watcher,launchd,composite_events,git_remote,projection_events,stage_output,scheduled_trigger,checks,fs_triggers,fs_processes,uv_updater}` |
| UI | `api/{app,routes}` — reads through `BoardView`/`ArtifactView`/`StageOutputView`, writes through `TaskControl`; never a driver |
| Edges | `app` (wiring), `cli` |

- `projection.py` — in-memory read model of the board; hydration from queues + event stream
- `artifacts_layout.py` — the single source of truth for artifact placement in the worktree (`next_attempt`, `artifacts_dir`); read by both the behavior and `WorktreeArtifactView`
- `ports/board.py` — the `BoardView` port through which the UI looks
- `ports/artifacts.py` — `ArtifactStore` (writing, phase 2) and `ArtifactView` (reading for the UI); phase 3 reads via `WorktreeArtifactView`
- `ports/workspace.py` — `Workspace.attach(task) -> WorkspaceHandle` (worktree + commit)
- `ports/forge.py` — `Forge.open_pull_request(...)` (landing proposes a PR)
- `ports/agent.py` — `AgentRunner.run(...)`, `AgentCatalog.get(name)`, `AgentSpec` (persona as data)
- `ports/repos.py` — `RepositoryRegistry.resolve(name) -> Path` (repo name → path)
- `behaviors/agent.py` — `ClaudeCliBehavior`: attach worktree → allocate attempt → run the agent → the worker commits
- `ports/source.py` — the `TaskSource` port (`poll`/`report_progress`/`finish`) + `Progress`/`FinishResult`
- `source_poller.py` — `SourcePoller`: the core that fills the inbox from the source (knows only ports)
- `pr_watcher.py` — `PrWatcher`: the core loop that claims a landed task out of `done/` once its PR has resolved (merged or closed unmerged) and archives it into `archived/`, dropping it from the board while keeping it gettable by id (knows only ports/models/ids)
- `healer.py` — `Healer`: the core loop assigned to the `failed/` queue (knows only ports/models/ids). Claims a failed task, runs the `healer` persona over a failure report, opens an issue via `IssueTracker`, and settles the task onto `healed/`
- `ports/issues.py` — `IssueTracker.open_issue(...)` (opens a fresh advisory issue, idempotent by marker) + `IssueRef`/`IssueError`
- `drivers/github_issues.py` — `GithubIssueTracker`: opens the healer's issue on GitHub over `GithubClient`, dedup by an embedded `<!-- harness-heal:<id> -->` marker
- `drivers/source_reflector.py` — `SourceReflectorSink(EventSink)`: event stream → projection into the source
- `drivers/github_client.py` — `GithubClient` (ABC), `Issue`, `FakeGithubClient`, `HttpGithubClient` (stdlib `urllib`)
- `drivers/github_source.py` — `GithubTaskSource`: issue → task, state → label
- `drivers/github_forge.py` — `GithubForge`: opens the real PR. Slug per task from
  the worktree's origin, base = the repo's default branch, `Closes #n` for an
  issue-born task, `ForgeError` on every failure path
- `drivers/launchd.py` — the background service on macOS: pure `wrapper_script`/
  `plist_bytes` builders (unit-tested) plus a thin `launchctl` shell; driven by
  `harness service install|uninstall|status`
- `ports/agent_admin.py` — `AgentAdmin` (write-side counterpart of `AgentCatalog`,
  for the admin UI): `list`/`read`/`write`/`delete`, plus `AgentFields`
  (raw strings in) and `AgentValidationError`
- `ports/workflow_admin.py` — `WorkflowAdmin` (write-side counterpart of
  `WorkflowRepository`, for the admin UI): `list`/`read_raw`/`write_raw`/`delete`
  over the file's exact text, plus `WorkflowValidationError`
- `ports/process_admin.py` — `ProcessAdmin` (write-side counterpart of
  `FilesystemProcessRepository`, for the admin UI): `list`/`read`/`write`/`delete`
  over the structured `ProcessFields`, plus `check_names()`/`sink_kinds()` so the
  form's dropdowns are populated through the port (`api/` imports no driver);
  `ProcessNotFound`/`ProcessAdminValidationError` mirror the agent admin's
- `drivers/mergeability_watcher.py` — `GithubMergeabilityWatcher(TaskSource)`,
  `kind="mergeability"`: `poll()` auto-updates a "behind" harness-owned PR
  (side effect, no task) and queues a "dirty" one as a resolver task on the
  same branch, deduped by `repo:pr:head_sha`
- `behaviors/resolve_conflict.py` — `ResolveConflictBehavior`: merges the base
  into the attached branch; a clean merge commits without spending an agent
  call, a real conflict runs the `resolve` persona then the worker commits
- `api/` — FastAPI board and admin UI; sees only `BoardView`, `ArtifactView`,
  `TaskControl`, `AgentAdmin`, `WorkflowAdmin` and `ProcessAdmin` — never a driver or `ArtifactStore`
- `ports/merge.py` — the `MergeChecker` port: `is_merged(task) -> bool | None` (`None`: no `data.pr`; raises on a transient failure — the caller must retry, never treat that as "not merged")
- `merge_reconciler.py` — `MergeReconciler`: the core that checks a `done` task's PR and archives it once merged (knows only ports/models, mirrors `source_poller.py`)
- `drivers/github_merge_checker.py` — `GithubMergeChecker`: reads `repo`/`number` straight off `task.data["pr"]` at check time, no per-repo construction
- `ports/issue_state.py` — the `IssueChecker` port: `is_open(task) -> bool | None` (`None`: no `data.source` this checker resolves; raises on a transient failure — the caller must retry, never treat that as "closed")
- `ports/updater.py` — the `Updater` port: `update() -> UpdateResult` (the UI-facing write-side of the version string the footer shows — runs `uv tool upgrade` and, on a version change, restarts the service; a failed restart folds into `UpdateResult.detail`, only a failed upgrade raises `UpdateError`)
- `drivers/uv_updater.py` — `UvUpdater`: the real `Updater` over `uv tool upgrade` + launchd `kickstart` (the same flow as `harness update`, reached from the board's Update button). Discovers `uv` itself; the installed entry point and the idle gate are injected by `cli.serve()` so the driver never imports back into `cli.py`
- `issue_reconciler.py` — `IssueReconciler`: the core that sweeps every live queue and archives a task whose source issue was closed or deleted out from under it (knows only ports/models/ids, mirrors `pr_watcher.py`)
- `drivers/github_issue_checker.py` — `GithubIssueChecker`: reads `repo`/`issue` straight off `task.data["source"]` at check time; a deleted issue (404) reads as "not open", one checker serves every repo the token can reach
- `drivers/github_issues_check.py` — `GithubIssuesCheck(Check)`: the inbound `harness:todo` scan as a process `Check` — lists issues by label across the repo registry, claims each via the label swap (`todo`→`queued`), and emits one provenance-stamped `Observation` (`data.source`) per issue. Registered as the `github-issues` action by closing a `GithubClient` + registry into a factory in `cli._process_sources`; `BUILTIN_CHECKS` stays client-free. The inbound half of ADR-0015's action seam (the `sink`/outbound half stays open)
- `ports/triggers.py` — the `Check` (ABC) protocol (`evaluate() -> list[Observation]`), `Observation` (optional `state_key` + task `data`), `CheckFactory` and `parse_interval` (a duration string → seconds). A trigger's condition is code behind this port; the schedule is data
- `drivers/scheduled_trigger.py` — `ScheduledTrigger(Trigger)`: composes an `interval` (data) × a `Check` (code) × a `target` (workflow **or** step). Cadence is a clock-gate on the interval bucket; `dedup_key` is bucket-keyed (`per-interval`) or state-keyed (`per-state`), never constant
- `drivers/checks.py` — the built-in `Check`s (`AlwaysCheck`, `DiskThresholdCheck`) and the `BUILTIN_CHECKS` registry mapping a check name → factory; a bespoke condition is a new factory registered by name
- `drivers/fs_triggers.py` — `FilesystemTriggerRepository`: reads `triggers/*.json` and builds one `ScheduledTrigger` per file, validating fast at load (`TriggerValidationError` on a bad `interval`, an unknown `check`, or a `target` naming neither a served workflow nor a known step) — exactly as `FilesystemAgentCatalog` reads `agents/*.json`
- `drivers/fs_processes.py` — `FilesystemProcessRepository`: reads `processes/*.json` (the top-level authoring aggregate — a nested `trigger`/`action`/`target`/`sink`) and **compiles each into a `ScheduledTrigger`**, validating fast (`ProcessValidationError`). A Process is a compile-time concept only; nothing under orchestration names it. The `sink` field is a forward-compat seam — parsed and validated but `none`-only in v1. The module-level `compile_process` is the single validator both the repository (startup) and `FilesystemProcessAdmin` (the write-side admin driver, on submit) run — `ProcessValidationError` carries an optional `field` so the admin maps a compile failure onto the right form field. See ADR-0015

## What is responsible for what

- **`TaskQueue`** — the inbox, the step queues, `done/`, `failed/` and `healed/` are
  all instances of the same port. Terminal states are simply queues that nobody
  consumes — with one exception: when a healer is wired, `failed/` gets exactly one
  reader (the `Healer` loop), and `healed/` becomes the never-consumed terminal
  (invariant 24).
- **The healer** (opt-in) is an agent assigned to the `failed/` queue. When a task
  fails, the `Healer` loop reads it, and if the `healer` persona judges it a fixable
  harness bug it opens a diagnostic issue on the harness repo via `IssueTracker`,
  then settles the task onto `healed/`. It works from the failure report (reason +
  history), no worktree. `harness init` writes `agents/healer.json`; `harness run
  --heal-repo <owner/repo>` enables it (needs `--agent claude`).
- **`claim()`** is an atomic `rename` into `<queue>/.processing/`. A single operation
  handles the lease, idempotency and provenance after a crash.
- **A step's concurrency is workflow config, not wiring.** `Workflow.max_parallel`
  (parsed from the optional `maxParallel: {step: N}` key in the workflow JSON,
  validated at load time by `FilesystemWorkflowRepository`) says how many tasks a
  step may work on at once; `Workflow.max_parallel_for(step)` defaults an absent
  entry to **1** — every workflow file written before this feature keeps behaving
  exactly as before. `Harness.run()` reads it to decide how many `_consumer_loop`
  coroutines to gather over the *same* `Consumer` for that step; `Consumer` and
  `claim()`'s atomicity are what keep two of those loops from ever claiming the
  same task, so `Consumer` itself needed no change beyond a read-only `step`
  property.
- **`END = "end"`** is a reserved node. It is not a "state with no outgoing edges" —
  a typo would then quietly pass for success.
- **A task has two workspaces** (phase 2). The **worktree** (`repository`/`worktree`)
  holds the code, versioned by the task's git branch. The **artifact folder**
  (harness-owned, not versioned into landing, readable by the UI) holds the
  plan/design/review. Deliberately separate — the worktree stays clean, the UI reads
  without git, `git clean` won't delete the artifacts. See the phase 2 spec for details.
- **`BehaviorResult(outcome, summary)`** is the behavior's return value. `outcome` is
  routed by the dispatcher; `summary` is the commit message, the history line, the PR
  body and the board text.
- **A task is a transaction.** The work lives in an isolated worktree/folder; at the
  end, **landing** lands the artifacts and opens a PR. The harness never touches
  `main` — it only proposes. The merge strategy is a human's call.
- **The agent** (phase 3) is a persona as data: `AgentSpec` carries the prompt, model,
  tools and `allowed_outcomes`. `AgentCatalog` maps a step's name to a spec (default
  identity name==step), the shared `AgentRunner` runs it. The model is per queue, not
  per class — adding an agent = a new file in the catalog, not a new class. `reviewer`
  may return `done`+`request_changes`, the others only `done`.
- **`RepositoryRegistry`** translates a repo name into a path on this machine
  (`repos.json`, machine-specific, uncommitted). The task carries only the name; the
  worktree path (`<worktrees_root>/<task_id>`) is derived by the harness. Reattaching a
  dirty worktree resets it to HEAD (reset-on-reattach).
- **Artifacts in the worktree** (phase 3): the agent writes them to `.artifacts/<id>/`,
  **the worker commits them** together with the code (they ride in the git history and
  in the PR). Attempt numbering (`<step>-NN`) is gapless — an unfinished attempt is
  discarded by reset-on-reattach and the re-run allocates the same number again.
- **`harness init`** writes the default agents (`agents/<step>.json`, the persona
  instructs the verdict block + writing an artifact) and an empty `repos.json`.
  `harness run` injects `ClaudeCliRunner`, `Filesystem{AgentCatalog,RepositoryRegistry}`,
  `GitWorkspace` and `WorktreeArtifactView`.
- **`TaskSource`** (phase 4) is the outside world of work control behind a single port
  with three verbs: `poll()` brings new tasks (a second inbox producer alongside
  `submit`), `report_progress`/`finish` project state outward. The GitHub adapter does
  it with labels, the filesystem one by moving JSON — a swap of driver, not
  surroundings. The task's origin travels with it in `task.data.source`; the outward
  projection is routed by `SourceReflectorSink` per `kind`, a foreign task (a different
  `kind` / no `source`) is silently ignored by the adapter, so tasks from
  `harness submit` pass through without a single outward call. `poll()` claims by
  flipping the label (GitHub's twin of the atomic `rename` in `fs_queue.claim()`) —
  "at most once" ingestion across restarts. Within the process, though,
  `list_issues` reads with read-after-write lag (unlike `rename`), so
  `GithubTaskSource` keeps an in-process ledger of claimed numbers (`_claimed`)
  so a fast poll won't claim the same issue twice.
- **`MergeReconciler`** is `SourcePoller`'s structural twin, pulling the outcome of
  already-landed work back *in* instead of pulling new work in: on its own
  `reconcile_interval` (default 300s — much longer than `source_interval`, since this
  is a housekeeping sweep, not latency-sensitive), it checks one `done` task's PR per
  tick (least-recently-checked first, via a `checkedAt` stamp on `task.data.pr`, to
  avoid starving on a single stubborn open PR) and, once merged, moves it into a new
  terminal queue `archived/` — a plain `TaskQueue`, so it gets `claim`/`transfer`/
  `recover` crash-safety for free, same as `done`/`failed`. `BoardProjection.archive()`
  drops the task out of every rendered column while keeping it in `_tasks`, so
  `GET /api/tasks/{id}` still resolves it with full history — declutter the live view,
  don't destroy the record. Only wired when a `merge_checker` is supplied to `build()`
  (real runs: gated on `GITHUB_TOKEN`, same as `GithubForge` — a fake/memory forge's
  synthesized `repo` placeholder is never checked against a live `MergeChecker`).
- **`IssueReconciler`** is `PrWatcher`'s structural sibling on the *source* side:
  where `PrWatcher`/`MergeReconciler` retire a `done` task once its *PR* resolves,
  the issue reconciler retires *any* board-visible task once its *source issue*
  resolves — closed by a human, closed automatically when its PR merged, or
  deleted. It sweeps every live queue (`inbox`, the step queues, `done`, `failed`)
  each tick via `IssueChecker.is_open(task)` and moves a closed-issue task into
  `archived/`. All-per-tick (like `PrWatcher`), on the slow `reconcile_interval`.
  Only wired when an `issue_checker` is supplied to `build()` (real runs gate it
  on `GITHUB_TOKEN`, exactly like the `MergeReconciler`); a submitted task with no
  `data.source`, or a foreign `kind`, is `None` from the checker and left alone.
- **`StageOutputView`** is a third, read-only UI surface alongside `BoardView`
  and `ArtifactView`: where `BoardView` shows *where* a task is and
  `ArtifactView` shows *what it produced*, `StageOutputView` shows *what the
  running stage is doing right now* — a bounded, in-memory, live-only tail
  (`drivers/stage_output.py`), gone once the stage ends. See ADR-0012.
- **A trigger** is a third way a task is born (alongside `harness submit` and a
  `TaskSource.poll()` over the outside world): a `Trigger` is a `TaskSource` that
  *produces* tasks and reflects nothing outward — it implements only `poll()`, its
  `report_progress`/`finish` are inherited no-ops, and it stamps no `data.source`, so
  the reflector ignores it exactly as it ignores a `harness submit` task.
  `ScheduledTrigger` composes an `interval` × a `Check` × a `target` (a workflow **or**
  a single step — the dispatcher does the placement, never the trigger). Its cadence is
  a **clock-gate**: `poll()` compares the current interval bucket (`floor(now /
  interval)`) against the last it fired and returns `[]` cheaply between fires, so the
  shared `source_interval` is only polling granularity, no new loop. Dedup is
  **bucket-keyed** (`per-interval`, one fire per period) or **state-keyed**
  (`per-state`, re-fire when an observed `state_key` changes), giving at-most-once per
  interval across restarts via the same `SourcePoller._seen` mechanism as GitHub
  ingestion. Triggers are **data** — `triggers/*.json`, each naming a built-in `check`
  from `BUILTIN_CHECKS` — with the schedule as data and the condition as code (the same
  split as personas). `FilesystemTriggerRepository` reads them and validates fast at
  build; `harness init` writes an empty `triggers/`.
- **A Process** is the operator's top-level authoring aggregate — the "tie it all
  together" surface. A `processes/*.json` file names four distinct roles: a
  **trigger** (cadence), an **action** (a named `Check` + params — the inbound
  producer), a **target** (a workflow **or** a step — the dispatcher still places
  it), and a **sink** (the outbound reflection destination). It is a *compile-time*
  concept: `FilesystemProcessRepository` compiles each file into a `ScheduledTrigger`
  (a `TaskSource`), so the whole runtime — `SourcePoller`, the dispatcher, the router
  — sees only the primitives it already knows, and every invariant holds unchanged.
  The `sink` is a `none`-only forward-compat seam reserving the schema slot for a
  later GitHub→Slack (source ≠ destination) split without a migration. `harness init`
  writes an empty `processes/`; the operator defines several, each an independent file.
  A Process compiles to the same `ScheduledTrigger` a bare `triggers/*.json` does, so
  both surfaces coexist — the Process is the richer primary surface, the trigger file
  the low-level primitive. A structured board editor exists (`ProcessAdmin`), wired in
  `serve()` like the agent/workflow editors — the operator assembles a process in the
  dashboard, validated by the same `compile_process`, picked up on the next run. See ADR-0015.

## Gotchas

- **Each queue has its own `.processing/`.** So after a crash there's no need to record
  anywhere where the task came from — recovery returns it to the queue it sits under.
- **Losing the race for `claim()` is not an error.** `os.replace` raises
  `FileNotFoundError`, the driver returns `None` and the loop takes the next task.
- **Broken JSON has no one to attribute history to.** The file is moved into `failed/`
  as-is, and only the event carries the reason.
- **`DummyBehavior` must return `done` deterministically.** `request_changes_once_at`
  returns `REQUEST_CHANGES` only on a given task's first pass through a given step;
  otherwise the loop would spin forever.
- **`build()` has default working drivers in-memory.** The substrate of the dummy
  behavior, like the dummy itself, is fake. `build` with a `catalog` switches the agent
  steps to `ClaudeCliBehavior`; the real run (`cli._run`) injects `GitWorkspace`,
  `ClaudeCliRunner`, `WorktreeArtifactView`, `Filesystem{AgentCatalog,Repository‑
  Registry}` and `GithubForge` — a swap of driver, not surroundings.
- **Landing is idempotent.** For an existing PR on a branch the forge returns the
  existing one (`GithubForge` matches on `head=owner:branch`). So a re-run after a
  crash won't open a second PR. The push is a plain `git push -u origin` — the task
  branch only ever moves forward, so a rejection is a real anomaly and must fail.
- **Landing needs a pushable remote.** `land` pushes the task branch before it
  proposes, so a registered repo with no `origin` cannot land — that is why the
  git e2e/smoke fixtures create a bare sibling repo and add it as `origin`.
- **A failed PR fails the task.** `GithubForge` raises `ForgeError` on a missing
  `GITHUB_TOKEN`, a non-GitHub origin or an API error, and the task lands in
  `failed/`. Deliberate: before this, `land` reported success while only writing
  to `prs.json`. Offline or in tests, use `--forge fake`.
- **The healer never writes back to `failed/`, so it cannot loop.** A heal claims a
  task out of `failed/` once and settles it to `healed/` — even its own failures
  (agent timeout, `IssueError`) become a `heal-failed` note there, never a re-queue.
  So there is no "healing the healer" recursion to guard, and `failed/` drains
  monotonically. `IssueTracker` is idempotent by the failed task id (an embedded
  `<!-- harness-heal:<id> -->` marker), so a crash before the settle won't file a
  second issue. Enabling the healer needs both a runner and a catalog (the persona
  is data); offline the issue tracker falls back to the in-memory fake so the loop
  still runs harmlessly.
- **`ArtifactStore.begin(task, step)` allocates the next attempt.** Writing into one
  slot belongs to one run; the second pass (the loop) gets a new subdirectory.
- **The service holds no secret.** launchd hands a process almost no environment, so
  `harness service install` generates a wrapper that resolves `GITHUB_TOKEN` at
  start-up — an explicit variable first, else `gh auth token` from the keyring. The
  plist itself never contains a token; a test asserts that. No token is not fatal:
  GitHub ingestion goes quiet and `harness submit` still works.
- **The LaunchAgent points at uv's shim** (`~/.local/bin/harness`), not at a
  virtualenv. `uv tool upgrade` rebuilds the tool environment but keeps the shim
  path, so an update never invalidates an installed service — it just needs a
  restart to be picked up. `service_entry_point()` falls back to
  `sys.prefix/bin/harness` for a from-source venv.
- **`sys.executable` is useless for locating the entry point.** Resolving it
  follows the venv's python symlink out to the base interpreter — with a
  uv-managed CPython that is `~/.local/share/uv/python/...`, where no `harness`
  script exists. Use `sys.prefix`.
- **The service `PATH` is explicit.** launchd's default `PATH` has no `git`, `gh` or
  `claude`, so the wrapper exports one built by `cli.service_path_entries` (venv bin
  first, then `~/.npm-global/bin`, `~/.local/bin`, `/usr/local/bin`, …). A "claude not
  found" failure deep in a run is usually this.
- **`Harness.recover()` always includes `done`, whether or not a reconciler is wired
  this run.** A `.processing/` file in `done/` can only have been left by a
  `MergeReconciler` claim, but it may outlive the run that created it (the operator
  could disable reconciliation between restarts). Gating it on `self.reconciler is not
  None` would leave such a task stuck forever; recovering an idle queue is a no-op, so
  it's unconditional and free.
- **A scheduled trigger's `dedup_key` is NON-constant — this is deliberate, don't
  "fix" it.** A trigger fires *fresh work* each period, so a constant key would make
  `SourcePoller._seen` suppress every fire after the first, forever (the opposite of a
  GitHub issue, whose key is its stable number). The key is bucket-keyed
  (`floor(now / interval)`), or state-keyed on `observation.state_key` for `per-state`
  — a `per-state` check with a `None` `state_key` is a usage error (every state would
  collapse into one), not a silent default. The clock-gate (`_last_bucket`) is only an
  optimization that short-circuits the poller's `sleep(0)` re-poll within a run; the
  persisted `dedup_key` + seeded `_seen` is the cross-restart guarantee. And that
  guarantee is **at-most-once per interval**, NOT suppress-while-in-flight: `_seen`
  remembers "ever ingested", not "currently open", so `per-state` re-fires if a state
  recurs after its task has terminated and `per-interval` can't coalesce an unresolved
  backlog — a live-task-consulting mode is a deliberate follow-up.
- **A Process and a bare trigger can express the same fire-and-forget automation
  two ways — this is deliberate, not a bug to dedupe away.** A `processes/*.json`
  compiles to the identical `ScheduledTrigger` a `triggers/*.json` does; the
  Process's payoff is its *nested roles* and its `sink` slot, the structure the
  future increments hang on. Folding `triggers/` into `processes/` is a later
  cleanup, not this increment's job.
- **Two Process seams are recorded in the spec but NOT built — don't mistake them
  for done.** (1) The **`sink`** accepts only `{"kind":"none"}`; there is no
  reflector registry and no GitHub/Slack sink driver yet, so a Process reflects
  nothing outward (its trigger stamps no `data.source`). (2) The **`github-issues`
  action** (scan issues by label as a `Check`) is deferred because a source-bearing
  check needs a `GithubClient` injected into a widened factory — the seam is
  `FilesystemProcessRepository.build()` growing a dependency bag, exactly as
  `_github_sources` obtains a client from `GITHUB_TOKEN`. Both are additive; see
  the spec's "sink seam" / "action seam" sections.

## Operator

Ondrej Pajgrt — "Ondrej" / "Rem". GitHub `onpaj`. Europe/Prague. The machine context
(NanoClaw, podman) is in `~/CLAUDE.md`.

A previous attempt at this idea lives in this repo's history at commit `7bc0e6e`;
`main` was emptied by commit `b7cab63` so it could be built phase by phase from scratch.
