# Resolve merge conflict on PR #105

Merged `origin/main` (tip `bbdf2bd Support cron expressions for trigger and
Process schedules alongside interval durations (#107)`) into
`harness/tsk_847b9cdf580e4fe1`. Git listed three conflicts:
`CLAUDE.md`, `tests/test_cli.py`, `tests/test_mergeability_e2e.py`.

## What the conflict actually was

This branch's own recent history (`fd81744`/`56c32a2`) fully removed
`GithubMergeabilityWatcher` and its `--watch-mergeability` CLI wiring in favor
of the check-based `GithubConflictsCheck` process action. `origin/main`'s new
work (`bbdf2bd`, PR #107) is unrelated: it adds cron-cadence support
(`parse_cron`/`CronSchedule`) to `ScheduledTrigger` alongside the existing
interval cadence. Both changes touched the same doc bullets/lines describing
`ScheduledTrigger`/`ports/triggers.py`, and the same default-workflow-name
assertion in `test_cli.py`, producing textual conflicts even though the two
features don't otherwise interact.

- **`CLAUDE.md`** (lines ~208-216): HEAD's bullets described the pre-cron,
  interval-only `ScheduledTrigger`/`ports/triggers.py`; `origin/main`'s
  described the same lines *plus* cron support. Verified the cron feature is
  real and already implemented (`parse_cron`/`CronSchedule` in
  `src/harness/ports/triggers.py`), so `origin/main`'s superset text is
  strictly more accurate — resolved to `origin/main`'s version.
- **`tests/test_cli.py`** (`test_run_with_no_workflow_flag_serves_default_and_resolver`,
  lines ~681-689): HEAD's assertion expected the default-served workflow set
  to be `{"default", "resolver"}` (stale, pre-dates the `DEFAULT_WORKFLOW =
  "development"` migration); `origin/main`'s expected `{"development",
  "resolver"}`. Confirmed `src/harness/cli.py:88` currently defines
  `DEFAULT_WORKFLOW = "development"`, so `origin/main`'s assertion is the one
  that matches the code — resolved to `origin/main`'s version (including its
  comment).
- **`tests/test_mergeability_e2e.py`** (delete/modify: `DU`, deleted on our
  side, trivially modified on `origin/main`'s — `Outcome.DONE` → `DONE`).
  Confirmed `src/harness/drivers/mergeability_watcher.py` no longer exists on
  this branch's HEAD (removed by `fd81744`), so this test — which imports that
  module — is dead regardless of `origin/main`'s edit. Kept the deletion
  (`git rm`).

## An additional, non-marked breakage found while verifying

`tests/test_processes_e2e.py` had no textual conflict, but the merge brought
in `origin/main`'s `a4be6d4 feat: open the closed Outcome enum into a
validated string` (part of the ADR-0018 "workflow-defined outcomes" work),
which deleted the `Outcome` enum from `src/harness/models.py` in favor of
plain string constants (`DONE`, `REQUEST_CHANGES`). `test_processes_e2e.py`
still did `from harness.models import Outcome, Task` and used
`AgentRun(Outcome.DONE, ...)` — valid on this branch's pre-merge HEAD (which
still had the `Outcome` enum) but broken once merged, since git's textual
3-way merge had no reason to touch that unrelated line. Fixed to match the
established post-refactor pattern used everywhere else in the test suite
(e.g. `tests/test_agent_behavior.py`): `from harness.models import DONE,
Task` and `AgentRun(DONE, ...)`.

## Verification

No `<<<<<<<`/`=======`/`>>>>>>>` markers remain anywhere in the tree (checked
recursively, excluding `.venv`; the only remaining hits are the literal
strings inside docs/tests that describe conflict markers, and `AUTO_MERGE`/
`MERGE_HEAD` bookkeeping). Full test suite:

```
.venv/bin/pytest -q
1293 passed, 1 skipped, 1 warning in 45.63s
```
