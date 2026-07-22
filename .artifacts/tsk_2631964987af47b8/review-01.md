# Review: archive a task off the board once its PR resolves

Reviewed `development-01.md`'s changes against `plan-03.md`/`design-03.md`/
`architecture-03.md` and the repo's invariants in `CLAUDE.md`.

## Spec conformance

All six FRs are met:

- **FR-1** (`Forge.pull_request_state`, tri-state open/merged/closed) —
  implemented identically across `GithubForge`, `FakeForge`, `MemoryForge`
  (checked `ports/forge.py`, `drivers/github_forge.py`, `drivers/fake_forge.py`).
- **FR-2** (PR reference persisted on the task) — `LandingBehavior` returns
  `BehaviorResult(..., data={"pr": {...}})`; `Consumer._deliver` merges it into
  `task.data` unconditionally (`consumer.py:95`), not branched on outcome —
  verified `test_consumer_has_no_branch_on_outcome_value` still passes.
- **FR-3** (periodic watcher, per-task failure isolation) — `PrWatcher.tick()`
  scans `done.list()`, skips tasks with no `data["pr"]`, wraps the forge call
  in a per-task try/except emitting `"pr_watch_error"` and continuing
  (`pr_watcher.py:47-51`).
- **FR-4** (resolved task leaves the board, stays gettable, survives restart)
  — `BoardProjection.archive()`/`_register()`/`snapshot()`'s tolerant lookup
  and `hydrate()`'s new `archived` parameter all present and exercised by
  `test_archive_then_snapshot_does_not_raise_for_other_tasks` and
  `test_hydrate_with_archived_queue_keeps_tasks_gettable_across_restart`.
- **FR-5** (audit trail) — `HistoryEntry(actor="pr_watcher", reason="pr
  merged"/"pr closed")` appended before the transfer; task file survives in
  `archived/`.
- **FR-6** (off by default) — `--pr-poll` defaults to `0.0`; `run()` only adds
  the watcher loop when `pr_poll_interval > 0`.

## Architecture conformance

- `pr_watcher.py` imports only `harness.models`, `harness.ids`, and
  `harness.ports.*` — guarded by the new
  `test_pr_watcher_imports_only_ports_and_models`, mirroring
  `test_source_poller_imports_only_ports_and_models`.
- Both mandatory fixes are present and exercised: `projection.py:108`'s
  `self._columns.get(task_id) == name` (was an unconditional `[...]`
  lookup), and `app.py`'s `recover()` now including `self._done`.
- Invariant #6 (`recover()` before `hydrate()`) preserved — order unchanged
  in `Harness.run()`.
- Invariant #7 (event carries both `task` and `queue`) — the `"archived"`
  event emits `task_id`, `resolution`, `queue="archived"`, `task=...`.
- Invariants #1–3 hold: `PrWatcher` is a new core component parallel to
  `SourcePoller`/`Dispatcher`, not routed through `dispatcher.py`/
  `consumer.py`; neither of those files was touched.
- `PullRequestState`/`get_pull_request` implemented uniformly across all
  three forge drivers and `GithubClient`/`FakeGithubClient`/`HttpGithubClient`.

## Completeness

Full acceptance-criteria coverage confirmed in `tests/test_pr_watcher.py`
(skip-without-ref, open-untouched, merged, closed-unmerged with distinct
reason, per-task error isolation, empty-queue no-op, lost-claim race),
`tests/test_projection.py` (archive/snapshot/restart), `tests/test_app.py`
(recover-fix regression test, end-to-end wiring with/without `--pr-poll`),
and `tests/test_smoke_git.py` (real git/filesystem: land → close PR →
archive → still `get()`-able).

Ran `.venv/bin/pytest -q`: **518 passed, 1 skipped, 0 failed** — matches
`development-01.md`'s report exactly.

## Correctness

No logic errors found. Failure isolation (per-task try/except in `tick()`),
crash safety (`recover()` fix, claim-before-transfer with lost-claim-race
handled via `claim() is None` early return), and idempotent PR-state mapping
(`FakeForge`/`GithubForge` defaulting missing `state`/`merged` to `"open"`/
`False` for pre-feature records) are all sound.

## Verdict

No issues found. The implementation matches the spec, respects every
architectural invariant, and has complete test coverage including the two
regressions the design called out as mandatory.
