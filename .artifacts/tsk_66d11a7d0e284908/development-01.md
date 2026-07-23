# Development: Poll for merged PRs and remove their completed tasks from the dashboard

Implements `plan-01.md`/`design-01.md`/`architecture-01.md` in full, following the
architecture's build order (§3.4) exactly, including the one fix it identified
(`MergeReconciler._release` must emit a `"rechecked"` event so the projection stays
fresh).

## What was implemented

**1. `BehaviorResult.data` + consumer merge** (`src/harness/models.py`,
`src/harness/consumer.py`)
`BehaviorResult` gained an optional `data: dict[str, Any] | None = None` field.
`Consumer._deliver` shallow-merges it into `task.data` on delivery — pure data
plumbing, no branch on `outcome` (invariant 2 untouched).

**2. `PullRequest.repo` + landing writes `task.data["pr"]`**
(`src/harness/ports/forge.py`, `src/harness/drivers/{github_forge,fake_forge,memory}.py`,
`src/harness/behaviors/landing.py`)
`PullRequest` gained a required `repo: str` field, populated by all three
implementations (`GithubForge` uses the real slug; `FakeForge`/`MemoryForge`
synthesize `local/<branch>` / `memory/<branch>` placeholders). `LandingBehavior`
returns `BehaviorResult(..., data={"pr": {repo, number, url, branch}})`.

**3. `MergeChecker` port + GitHub driver** (`src/harness/ports/merge.py` [new],
`src/harness/drivers/github_client.py`, `src/harness/drivers/github_merge_checker.py`
[new], `src/harness/drivers/memory.py`)
`MergeChecker.is_merged(task) -> bool | None` — `None` for no `data.pr`, raises on
transient failure. `GithubClient` gained `get_pull_request`/`PullRequestDetail` on
both `FakeGithubClient` (+ `merge_pull_request()` test helper) and `HttpGithubClient`.
`GithubMergeChecker` reads `repo`/`number` off `task.data["pr"]` at check time.
`FakeMergeChecker` (in `drivers/memory.py`) gives unit tests direct control.

**4. `merge_reconciler.py`** (new, core — imports only `harness.models`/`harness.ports.*`/
`harness.ids`)
`MergeReconciler.tick()`: selects the least-recently-checked `done` task carrying
`data.pr` (via a `checkedAt` stamp, avoiding starvation on a stubborn open PR),
claims it, checks it. Merged → transfers to `archived`, appends a history entry,
emits `"archived"`. Not merged / checker error → releases back into `done` with
`_release`, which — per the architecture's fix — emits `"rechecked"` so the
projection sees the updated `checkedAt`. Returns `True` only on an actual archive
(never on "examined, still open"), matching the "don't tight-spin" requirement.

**5. Wiring** (`src/harness/app.py`)
`HarnessLayout.archived`; `Harness.archived`/`.reconciler` (both `None` by default);
`build(merge_checker=...)` constructs the `archived` `FilesystemTaskQueue` and
`MergeReconciler` only when a checker is supplied; `recover()` now includes `done`
**unconditionally** (a `.processing/` file there can only come from a reconciler
claim, but must recover even if reconciliation is off this run); `run()` gained
`reconcile_interval: float = 300.0` and a `_reconcile_loop` gathered alongside the
dispatcher/consumer/source loops; `projection.hydrate(..., archived=self.archived)`.

**6. `BoardProjection.archive()` + `ProjectionSink` routing**
(`src/harness/projection.py`, `src/harness/drivers/projection_events.py`)
`archive(task)` stores the task in `_tasks` but drops it from `_columns`, so
`snapshot()` no longer lists it but `get()` still resolves it. `hydrate()` gained an
`archived: TaskQueue | None` param that hydrates the same way. `ProjectionSink`
routes the `"archived"` event name to `archive()` instead of `apply()`.
**Bug found and fixed along the way**: `snapshot()` iterated `self._tasks.items()`
and unconditionally indexed `self._columns[task_id]` — this raised `KeyError` the
moment `_tasks` could legitimately contain an entry absent from `_columns` (exactly
the archived-task case). Fixed to iterate `self._columns.items()` instead.

**7. `cli.py` wiring + architecture tests + e2e test**
`_build_merge_checker(args)` — gated on `GITHUB_TOKEN`, same condition as
`_build_forge`, independent of `--forge` (so `--forge fake` never gets a live
checker paired with its non-GitHub `repo` placeholders). New `--reconcile-poll`
flag (default 300.0) threaded through `serve()` into `harness.run(reconcile_interval=...)`.
`tests/test_architecture.py` gained `test_reconciler_imports_only_ports_and_models`
and `test_orchestration_does_not_import_merge_port` (invariant 24).
`tests/test_merge_reconciler_e2e.py` (new) drives land → merge (fake) → reconcile →
archived, on in-memory drivers, plus a backward-compatibility test
(`merge_checker=None` → `done` tasks are never touched).

**8. `CLAUDE.md`**
New invariant 24 (`MergeChecker` isolation), module map entries for `ports/merge`,
`merge_reconciler.py`, `drivers/github_merge_checker.py`, a "what's responsible for
what" paragraph on `MergeReconciler`, and a gotcha on `recover()` including `done`
unconditionally.

## Files created

- `src/harness/ports/merge.py`
- `src/harness/drivers/github_merge_checker.py`
- `src/harness/merge_reconciler.py`
- `tests/test_github_merge_checker.py`
- `tests/test_merge_memory.py`
- `tests/test_merge_reconciler.py`
- `tests/test_merge_reconciler_e2e.py`

## Files changed

`src/harness/{models,consumer,projection,app,cli}.py`,
`src/harness/ports/forge.py`,
`src/harness/behaviors/landing.py`,
`src/harness/drivers/{github_client,github_forge,fake_forge,memory,projection_events}.py`,
`tests/test_{app,architecture,cli,consumer,fake_forge,forge_memory,github_client,
github_forge,landing_behavior,projection,projection_events}.py`, `CLAUDE.md`.

## How to verify

```sh
.venv/bin/pytest -q
```

516 passed, 1 skipped (the opt-in `HARNESS_SMOKE_CLAUDE=1` real-`claude` smoke).
Everything runs on in-memory drivers / `FakeClock` except the pre-existing
`test_smoke.py`/`test_smoke_git.py`/`test_smoke_github.py`, which still pass
unchanged.

Key new/extended test files to spot-check:
- `tests/test_merge_reconciler.py` — starvation-avoidance round robin, checker-error
  retry priority, crash-then-recover.
- `tests/test_merge_reconciler_e2e.py` — full land → merge → archive flow and the
  `merge_checker=None` backward-compatibility path.
- `tests/test_app.py` — `build()` with/without `merge_checker`, `run()` driving a
  real archive, `recover()` picking up a stranded `done/.processing/` task.
- `tests/test_architecture.py` — the new invariant-24 assertions.

Manual sanity check of the CLI surface:
```sh
.venv/bin/python -c "from harness.cli import main; main(['run', '--help'])" | grep reconcile-poll
```
