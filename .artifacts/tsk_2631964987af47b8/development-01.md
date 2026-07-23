# Development: archive a task off the board once its PR resolves

Implements `plan-03.md`/`design-03.md`/`architecture-03.md` verbatim — a
periodic `PrWatcher`, modeled on `SourcePoller`, that scans `done/` for tasks
whose PR has resolved (merged or closed unmerged) and moves them into a new
`archived/` queue, dropping them from every board column while keeping them
`get()`-able by id. Both mandatory fixes the design called out
(`BoardProjection.snapshot()`'s `KeyError`, `Harness.recover()`'s missing
`done` queue) are included in the same commit as the feature that exposes them.

## Files changed

**New:**
- `src/harness/pr_watcher.py` — `PrWatcher` core component. `tick()` scans
  `done.list()`, skips tasks with no `data["pr"]`, queries
  `Forge.pull_request_state`; on resolution it claims the task out of `done`,
  appends a `HistoryEntry` (`actor="pr_watcher"`, `reason="pr merged"`/`"pr
  closed"`), sets `status="archived"`, transfers into `archived`, and emits an
  `"archived"` event. A per-task `try`/`except` around the forge call emits
  `"pr_watch_error"` and isolates one bad check from the rest of the tick.
  Touches only `harness.models`, `harness.ids` (for `new_lock_id()`, exactly
  like `dispatcher.py`/`consumer.py` need it to claim) and `harness.ports.*` —
  never a driver.
- `tests/test_pr_watcher.py` — unit coverage: skip-without-pr-ref, open→untouched,
  merged→archived, closed-unmerged→archived (with the right `reason`),
  per-task forge-error isolation (one bad task doesn't stop the rest of the
  tick), empty-queue no-op, and a lost-claim-race case (a `RaceQueue` fake
  whose `claim()` always returns `None`, simulating a concurrent tick).

**Modified:**
- `src/harness/ports/forge.py` — new `PullRequestState` enum
  (`OPEN`/`MERGED`/`CLOSED`) and `Forge.pull_request_state(task) ->
  PullRequestState` abstract method.
- `src/harness/drivers/github_client.py` — new `PullRequestDetail` dataclass
  and `GithubClient.get_pull_request(repo, *, number)` abstract method.
  `FakeGithubClient` tracks per-number `(state, merged)`, defaulting to
  `("open", False)`, plus a `close_pull_request(number, *, merged)` test
  helper. `HttpGithubClient.get_pull_request` does `GET
  /repos/{repo}/pulls/{number}` and reads `state`/`merged` straight off the
  response.
- `src/harness/drivers/github_forge.py` — `GithubForge.pull_request_state`:
  resolves the slug the same way `open_pull_request` does, reads
  `task.data["pr"]["number"]`, calls the client, maps the result to
  `PullRequestState`, and raises `ForgeError` (via the existing `_explain`
  wrapper) for a missing token, an unresolvable repo, a task with no PR
  reference, or any API failure.
- `src/harness/drivers/fake_forge.py` — `prs.json` records gain `state`/`merged`
  fields (defaulting to `"open"`/`false` when absent, so a pre-feature fixture
  file still reads as "open"). New `pull_request_state` and a
  `close_pull_request(branch, *, merged)` test/smoke helper.
- `src/harness/drivers/memory.py` — `MemoryForge` gains an in-memory
  `_states` map (branch → `(PullRequestState, merged)`), `pull_request_state`,
  and a `close(branch, *, merged)` test helper.
- `src/harness/models.py` — new `ARCHIVED = "archived"` status constant;
  `BehaviorResult` gains an optional `data: dict[str, Any] | None = None`
  field the consumer merges into `task.data` on delivery.
- `src/harness/consumer.py` — `_deliver` now does
  `merged_data = {**task.data, **result.data} if result.data else task.data`
  before writing the updated task. This is an `if`/ternary, not a comparison
  derived from `outcome`, so it doesn't trip
  `test_consumer_has_no_branch_on_outcome_value` (verified — the test still
  passes unmodified).
- `src/harness/behaviors/landing.py` — after `open_pull_request` succeeds,
  `LandingBehavior.run` returns `BehaviorResult(Outcome.DONE, ..., data={"pr":
  {"number", "url", "branch"}})` so the task remembers its PR reference
  without re-deriving it from git.
- `src/harness/projection.py` — **[mandatory fix]** `snapshot()`'s column loop
  now does `self._columns.get(task_id) == name` instead of the unconditional
  `self._columns[task_id] == name`, so a task with no column entry (archived)
  no longer raises `KeyError` for the whole board. New `archive(task)` method
  and a shared `_register(task)` primitive (keeps the task in `_tasks`,
  removes it from `_columns`) used both by `archive()` and by `hydrate()`'s
  new optional `archived: TaskQueue | None = None` parameter, so a task
  archived in a previous run stays `get()`-able across a restart.
- `src/harness/drivers/projection_events.py` — `ProjectionSink.emit` now
  branches on `name == "archived"` to call `projection.archive(task)` instead
  of `apply(column, task)`; every other event is unaffected.
  `SourceReflectorSink` needed no change (it already ignores any event name
  outside `{"dispatched", "finished", "failed"}`).
- `src/harness/app.py` — `HarnessLayout.archived` (parallel to `done`/`failed`).
  `Harness` gains `archived`/`pr_watcher` (constructor params, stored as
  `self._archived`/`self.pr_watcher`). **[mandatory fix]** `recover()` now
  includes `self._done` in the queues it recovers — `PrWatcher` is the first
  thing to ever claim out of `done/`, so a crash between its `claim()` and
  `transfer()` would otherwise strand a task in `done/.processing/`,
  invisible to both `done.list()` and `hydrate()`. `run()` gains a
  `pr_poll_interval: float = 0.0` parameter, passes `archived=self._archived`
  into `hydrate()`, and only starts `_pr_watcher_loop` when
  `pr_poll_interval > 0`. `build()` always constructs the `archived` queue and
  a `PrWatcher` (cheap and side-effect-free until ticked, same posture as
  `landing` always being built).
- `src/harness/cli.py` — new `--pr-poll <seconds>` flag on `harness run`
  (default `0.0` = disabled, following the `--api-port 0` convention);
  threaded through `_run`/`serve` into `harness.run(pr_poll_interval=...)`.
- `tests/test_architecture.py` — new
  `test_pr_watcher_imports_only_ports_and_models`, the import-boundary guard
  for the new core module. It allows `harness.models`, `harness.ids` (needed
  for `new_lock_id()`, same as `dispatcher.py`/`consumer.py`) and anything
  under `harness.ports` — never a driver.
- `tests/test_smoke_git.py` — extended the existing real-git/real-filesystem
  smoke: after the task lands, a second `Harness` (same `root`, a `FakeForge`)
  closes the PR via `close_pull_request(branch, merged=True)` and runs with
  `pr_poll_interval=0.01`; asserts the task file moves from `done/` to
  `archived/` on disk and that the live `BoardProjection` drops it from every
  column while `get()` still returns it.
- Other test files (`test_github_client.py`, `test_github_forge.py`,
  `test_fake_forge.py`, `test_forge_memory.py`, `test_models.py`,
  `test_consumer.py`, `test_landing_behavior.py`, `test_projection.py`,
  `test_projection_events.py`, `test_app.py`, `test_cli.py`) got matching unit
  tests for each of the above changes, plus fixes to a few `test_cli.py` fakes
  whose signatures needed the new `pr_poll_interval` parameter.

## Verify

```sh
.venv/bin/pytest -q
```

518 passed, 1 skipped (the opt-in `HARNESS_SMOKE_CLAUDE=1` test), 0 failed.

Notable individual tests to point at:
- `tests/test_pr_watcher.py` — the new component in isolation.
- `tests/test_projection.py::test_archive_then_snapshot_does_not_raise_for_other_tasks`
  — the `KeyError` regression this design specifically calls out.
- `tests/test_app.py::test_recover_returns_a_task_stranded_between_pr_watcher_claim_and_transfer`
  — the crash-safety fix to `Harness.recover()`.
- `tests/test_app.py::test_pr_watcher_archives_a_resolved_task_end_to_end` and
  `test_pr_watcher_loop_does_not_run_when_interval_is_zero` — the wiring
  (`pr_poll_interval`) end to end, both enabled and at its off-by-default value.
- `tests/test_smoke_git.py::test_task_lands_as_pull_request_on_real_git` — real
  git/filesystem/forge coverage of the whole feature, land → close PR →
  archive → still `get()`-able.

Manual smoke (optional, not required by the tests above): `harness run
--pr-poll 300 --forge fake` starts the watcher on a 5-minute interval; `harness
run` with no flag behaves identically to before this change (watcher never
ticks).
