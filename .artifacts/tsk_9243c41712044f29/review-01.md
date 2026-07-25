# Review — manual "Add issue" button on the Ahanas board

Reviewed `d86aba7` against `plan-01.md`/`design-01.md` (as corrected by
`architecture-01.md`) and against the acceptance criteria in the task
description.

## Wiring correction (architecture-01.md §1) — verified applied

- `ports/issue_import.py`: `IssueImportResult`, `IssueImport` (ABC),
  `NullIssueImport`, `IssueImportFactory` — matches the spec exactly.
- `app.py::build()`: `issue_import_factory` param invoked at line ~488,
  confirmed *after* `events` is wrapped in `CompositeEventSink` (line 430)
  and after `inbox`/`step_queues`/`done`/`failed`/`healed_queue`/`archived`
  are all constructed (lines 437-464) — the exact ordering risk the
  architecture doc flagged. `Harness.issue_import` is a public, always-
  concrete attribute, falling back to `NullIssueImport()`.
- `cli.py::_issue_import_factory`: closes over the already-built
  `github_client`/`registry` (no second client), reuses
  `--github-workflow`/`--github-step`/`DEFAULT_WORKFLOW` defaulting and the
  same `worktree_root` computation as `_github_sources`. Returns `None`
  without a token. `_run` passes it into `build(...)`; `serve()` threads
  `harness.issue_import` into `create_app`.
- Two targeted regression tests exist for exactly the risks the architecture
  doc called out: `test_build_with_issue_import_factory_receives_the_harness_own_queues`
  (factory gets `build()`'s *actual* queues, not fresh ones) and
  `test_build_issue_import_events_reach_the_projection` (the emitted
  `"ingested"` event reaches `BoardProjection` — catches the CompositeEventSink-
  ordering bug before it could exist).

## Acceptance criteria — all met

- Visible "Add issue" button, reusing existing `.btn`/`page-header__action`
  classes and `--text`/`--failed-fg` tokens (no new colors) — light/dark
  covered by the existing CSS variable scheme.
- Dialog textarea splits on `[,\s]+` (`_split_refs`), covering comma/space/
  newline separation — tested.
- `GithubClient.get_issue` is a point lookup by number, bypassing the label
  scan entirely — no-label issues are fetched fine.
- `data.source` stamped field-for-field identical to
  `GithubTaskSource.poll()`'s (`kind`/`repo`/`issue`/`url`), same `dedup_key`
  helper, so outward label reflection needs no change — confirmed by direct
  comparison with `github_source.py:161-179`.
- Lands via a plain `inbox.put` + `"ingested"` event (`SourcePoller.tick()`'s
  exact shape) — no queue placement, invariant #35 intact.
- Goes through the new `IssueImport` port; `api/routes.py` imports only the
  port. `test_architecture.py::test_orchestration_does_not_import_issue_import_port`
  guards dispatcher/consumer never import it, mirroring invariants #23/#32/#34.
- Invalid/unregistered/not-found/network-error refs each return `ok=False`
  with a clear, distinct message; `add()` never raises; the route processes
  refs sequentially so one bad ref doesn't abort the batch — tested
  (`test_import_mixed_batch_shows_each_outcome`, and the driver's error-path
  tests).
- Idempotent duplicates: dedup scan spans all six queues (inbox, every step
  queue, done, failed, healed, archived) — a deliberate superset of the
  pollers'/reconcilers' usual four, documented as intentional. Same-batch
  duplicate is handled correctly because refs are processed sequentially
  (verified by `test_add_same_batch_duplicates_the_second_call_sees_the_first`
  and the route-level mixed-batch test).

## Correctness checks

- `GithubIssueImportService._resolve_repository` scans `registry.names()`,
  skipping any name that raises `RepositoryNotFound`, and compares via the
  injectable `slug_of` — consistent with `GithubConflictsCheck`/
  `GithubIssuesCheck`'s existing pattern.
- Best-effort label claim swallows any exception without failing the
  already-real task — matches the plan's intent and is tested
  (`test_add_label_failure_does_not_prevent_the_task_from_being_queued`).
- `HttpGithubClient.get_issue` follows the same 404→`None` idiom as
  `get_issue_state`; every other error propagates, matching the ABC's
  documented contract.

## Test run

Ran the full suite with the ambient `HARNESS_HEAL_REPO`/`GITHUB_TOKEN` env
vars from this shell unset (they don't belong to the task and were
perturbing unrelated `test_cli.py` heal/label-issue-finisher tests via env,
not via this diff):

```
env -u HARNESS_HEAL_REPO -u GITHUB_TOKEN .venv/bin/pytest -q
1391 passed, 1 skipped, 1 warning
```

`test_architecture.py` (26 tests, including the two new/updated guards)
passes standalone as well.

## Verdict

No functional requirement is unmet, no architecture conflict, no missing
required tests, no correctness bug found. The implementation is a faithful,
well-tested realization of the corrected architecture.

```json
{"outcome": "done", "summary": "Implementation conforms to plan/design/architecture: IssueImport port + GithubIssueImportService built inside build() via a cli.py-supplied factory exactly as architecture-01.md specified, with the ordering (CompositeEventSink before factory invocation) and queue-identity risks it flagged both covered by dedicated regression tests. All acceptance criteria verified against the code (no-label fetch, data.source parity with GithubTaskSource, inbox placement, port-only api/ access guarded by test_architecture.py, per-ref error reporting, six-queue idempotency including same-batch dedup). Full suite (1391 tests) passes; the 8 failures seen on a first run were caused by ambient HARNESS_HEAL_REPO/GITHUB_TOKEN env vars in the shell, not the diff — confirmed clean with those unset."}
```
