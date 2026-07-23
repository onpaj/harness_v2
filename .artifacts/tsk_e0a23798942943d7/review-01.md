# Review — the `healer` workflow

Verdict: **done**. The implementation matches `plan-01.md`/`design-01.md`/
`architecture-01.md` closely — I checked it file-by-file against the
architecture step's 10-step build order and every FR's acceptance criteria,
not just the development artifact's summary.

## What I checked

- **Full test suite**: `.venv/bin/pytest -q` → 518 passed, 1 skipped, no
  failures, no warnings beyond a pre-existing deprecation notice.
- **FR-1/FR-2 (`FailedQueueTaskSource`, dedup)** — `drivers/failed_queue_source.py`
  reads `failed.list()` only (never claims/mutates), filters by
  `workflow_template == target_workflow`, and derives
  `dedup_key("failed-queue", failure.id, len(failure.history))` exactly as
  specified. `tests/test_failed_queue_source.py` covers all of FR-1/FR-2's
  acceptance criteria explicitly, including the restart-then-fail-again case
  (`test_restart_then_fail_again_yields_a_second_distinct_task`) and the
  poller-level double-heal guard
  (`test_repolling_unchanged_failed_queue_yields_no_second_task_via_poller`).
- **FR-3 (`healer` workflow, `Outcome` extension)** — `Outcome.BUG_CONFIRMED`/
  `NOT_A_BUG` added purely additively; `HEALER_DEFINITION` in `cli.py` matches
  the plan's transition table exactly; `_allowed_outcomes_for` computes
  `diagnose`'s `allowed_outcomes` dynamically from the workflow graph, so it's
  automatically `["bug_confirmed", "not_a_bug"]` with no hardcoding.
- **FR-4 (`Forge.open_issue`)** — `ports/forge.py`'s `FiledIssue`/`open_issue`
  is symmetric with `PullRequest`/`open_pull_request`. All three concrete
  forges implement it: `MemoryForge` (idempotent by `task.id`), `FakeForge`
  (idempotent by `task_id` field in `issues.json`, sibling to `prs.json`),
  `GithubForge` (idempotent via the new `GithubClient.find_issue` body-marker
  scan before `create_issue`, exactly the `find_pull_request`/
  `create_pull_request` shape). `ForgeError` is raised on the same failure
  classes as `open_pull_request` (no token, unresolvable repo, non-GitHub
  origin, API error) — verified in `tests/test_github_forge.py`'s
  `open_issue` section, which mirrors the PR test suite one-for-one.
- **FR-5 (diagnostic context)** — `_diagnostic_report`/`_failure_point` in
  `FailedQueueTaskSource` populate `data["request"]` and
  `data["failed_task"]` from the `FAILED`-labelled history entry, with no
  change to `compose_prompt`. Confirmed by reading `_failure_point` against
  `Dispatcher._fail`/`Consumer._fail` (both write exactly one `to_step=FAILED`
  entry) — the scan is correct, not just plausible.
- **FR-6 (healer's own workspace)** — `_build` sets `repository=healer_repo`
  on the healer task (not the failed task's own repository), matching FR-6
  and the data model in `design-01.md`.
- **FR-7 (two workflows, one process)** — `column_order`/`BoardProjection`
  are correctly variadic with a single call site
  (`BoardProjection(*active_workflows)`); `build()` unions `step_queues`
  across active workflows; `StepCollisionError`/`_assert_no_step_collision`
  is implemented and tested (`test_heal_with_colliding_step_name_raises`,
  and mirrored in `test_cli.py`'s
  `test_run_heal_with_step_collision_fails_cleanly`). `heal=False` byte-
  identical behavior is guarded by the full pre-existing `test_app.py` suite
  passing unchanged plus an explicit
  `test_heal_false_builds_byte_identical_to_today`.
- **FR-8 (CLI wiring)** — `--heal`/`--healer-repo` added to `run`; the
  fail-fast `sys.exit(2)` check in `_run()` plus `build()`'s own `ValueError`
  backstop, both tested. `_init()` always seeds `workflows/healer.json` and
  `agents/diagnose.json` (never `agents/file_issue.json`), independent of
  `--workflow`, tested in `test_cli.py`.
- **Invariants** — verified the generic AST-based architecture tests
  (`test_behaviors_import_only_ports_not_drivers`,
  `test_only_app_and_cli_wire_drivers`,
  `test_orchestration_does_not_import_source_port`,
  `test_consumer_has_no_branch_on_outcome_value`) all glob their target
  directories, so the new `behaviors/file_issue.py` and
  `drivers/failed_queue_source.py` are automatically covered without needing
  new dedicated architecture-test entries — confirmed by reading the glob
  patterns rather than assuming.
- **E2e coverage** (`tests/test_healer_e2e.py`) — drives the full loop
  (poller → dispatcher → consumers) with a scripted failure at `development`
  and a scripted `diagnose` outcome. Covers: `bug_confirmed` → issue filed →
  `done/`; `not_a_bug` → `done/` with zero issues; re-polling doesn't double
  heal; a healer task's own failure does not spawn a third (meta-healer)
  task. This is exactly the plan's rough-plan step 7 and FR-7's/FR-2's
  acceptance criteria exercised together, not just in isolation.

## Non-blocking observation (not requesting changes)

`_init()` writes `workflows/{args.workflow}.json` and
`workflows/healer.json` as two separate `if not exists` checks
(`cli.py:112-127`). If an operator ran `harness init --workflow healer`
(nothing stops this — `invalid_workflow_name` doesn't reserve the name), the
first write would occupy `workflows/healer.json` with the *primary*
workflow's definition (`plan → … → land`), and the healer-seeding block would
then see the file already exists and skip writing the real
`HEALER_DEFINITION` — silently leaving `--heal` non-functional later. This is
a genuine edge case, but: it requires a user to deliberately name their
primary workflow "healer" (a self-defeating, paradoxical choice), it's not
covered by any FR or acceptance criterion in the plan/design/architecture,
and none of the three prior steps flagged the name as reserved. Worth a
one-line guard (reject `--workflow healer` in `invalid_workflow_name`, or in
`_init`) as a future cleanup, not a reason to send this back.

## Conclusion

Every functional requirement's acceptance criteria is met and tested, the
architecture's guarded invariants hold (confirmed by reading the actual test
implementations, not assuming), and the full suite is green with substantial
new coverage (47 tests) mirroring the shape of every existing analogous test
file. No correctness bugs, no missing required tests, no conflict with the
architecture.
