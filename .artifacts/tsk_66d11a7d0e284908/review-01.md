# Review: Poll for merged PRs and remove their completed tasks from the dashboard

Reviewed `development-01.md` against `plan-01.md`/`design-01.md`/`architecture-01.md` and
the actual diff (`git diff HEAD~1`), file by file.

## Conformance to spec (plan-01.md)

- **FR-1** (`PullRequest.repo`, `task.data["pr"]`): `PullRequest.repo` added as a
  required field, populated by `GithubForge` (real slug), `FakeForge`
  (`local/<branch>`), `MemoryForge` (`memory/<branch>`). `LandingBehavior` returns
  `BehaviorResult(..., data={"pr": {...}})`. Covered by
  `test_landing_behavior.py::test_result_carries_structured_pr_identity` and
  `test_github_forge.py`. Matches spec exactly.
- **FR-2** (`MergeChecker`): `ports/merge.py` + `GithubMergeChecker` +
  `GithubClient.get_pull_request`/`PullRequestDetail` on both fake and HTTP clients +
  `FakeMergeChecker`. `None`/`True`/`False`/raise semantics all present and tested
  (`test_merge_reconciler.py`, `test_github_merge_checker.py`).
- **FR-3** (reconciliation loop): `merge_reconciler.py::MergeReconciler.tick()` — one
  candidate per tick, least-recently-checked selection via `checkedAt`, claims via
  `TaskQueue.claim`, archives on merged, releases with a stamp otherwise, `True` only
  on an actual archive. All five spec ACs are individually tested: merge→archive+event,
  no-`data.pr`→untouched, open PR→stays+rechecked, checker exception doesn't stop the
  loop, crash-between-claim-and-transfer recovers via `done.recover()`. Starvation
  avoidance (the plan's own concern) has two dedicated tests.
- **FR-4** (`BoardProjection.archive()`): drops from `_columns`, keeps in `_tasks`;
  `hydrate()` gained the matching `archived` param. `snapshot()`'s pre-existing
  `KeyError` risk (iterating `_tasks` but indexing `_columns`) was correctly identified
  and fixed by iterating `_columns` instead — this was a latent bug the feature would
  otherwise have tripped over immediately. `/api/board` and `/api/tasks/{id}` need no
  route change, confirmed by the e2e test asserting exactly that. Covered by
  `test_projection.py` (four new tests) and `test_merge_reconciler_e2e.py`.
- **FR-5** (no new operator surface): confirmed — no new endpoint, no new column, no
  manual trigger. `--reconcile-poll` only controls the existing interval knob, same
  shape as `--source-poll`.

## Adherence to architecture (architecture-01.md)

- The one concrete fix the architecture step identified — `MergeReconciler._release`
  must emit a `"rechecked"` event so the projection sees the updated `checkedAt` — is
  implemented exactly as specified, routed through the existing `apply("done", ...)`
  path with no new `ProjectionSink` branch needed for it.
- Build order (§3.4) was followed: `BehaviorResult.data` → `PullRequest.repo` →
  `MergeChecker`/`GithubClient` → `merge_reconciler.py` → `app.py` wiring → projection
  → `cli.py`/architecture tests/e2e → `CLAUDE.md`.
- Invariant 24 (`MergeChecker` touched only by `MergeReconciler` and wiring) is written
  into `CLAUDE.md` and enforced by two new `test_architecture.py` assertions:
  `test_reconciler_imports_only_ports_and_models` and
  `test_orchestration_does_not_import_merge_port`. Both pass.
- `Harness.recover()` includes `done` **unconditionally**, exactly as the architecture
  flagged as the easy-to-get-wrong part (gating it on `reconciler is not None` would
  have silently broken crash recovery) — confirmed unconditional in the diff.
- `reconcile_interval` defaults to 300s, distinct from `source_interval` (30s) and
  `poll_interval` (0.2s), threaded through `cli.py --reconcile-poll` → `serve()` →
  `Harness.run()`, matching the architecture's decision 4.
- `archived/` is a plain `FilesystemTaskQueue`, only constructed in `build()` when
  `merge_checker` is supplied — no cost when reconciliation isn't wired.

## Completeness

All plan ACs have corresponding tests; the reconciler's tick-return-value contract
(`True` only on actual archive), the crash-recovery path, and the starvation-avoidance
mechanism are each independently tested rather than only exercised incidentally by the
e2e test. `516 passed, 1 skipped` (the pre-existing opt-in `HARNESS_SMOKE_CLAUDE`
smoke) — verified by re-running `.venv/bin/pytest -q` myself.

## Correctness

No logic errors found. Error handling matches the spec's explicit contract (raise on
transient failure, don't stamp `checkedAt` on error so the task gets retry priority).
No concurrency issues beyond what the existing `claim`/`transfer`/`recover` machinery
already handles, which this feature reuses rather than reimplementing. The
`BoardProjection.snapshot()` bug found and fixed along the way is a genuine
improvement, not scope creep — it was a real crash waiting to happen the moment any
task existed in `_tasks` without a `_columns` entry, which archiving is the first
scenario in the codebase to produce.

## Verdict

Implementation is a faithful, complete, and correct realization of
plan-01.md/design-01.md/architecture-01.md. No changes requested.
