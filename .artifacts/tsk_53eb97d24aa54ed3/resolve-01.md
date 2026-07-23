# Resolve merge conflict on PR #55

Merged `origin/main` (tip `9010dba chore(release): 0.9.1`) into
`harness/tsk_e0a23798942943d7`. 15 files had textual conflicts:
`src/harness/{app,cli,models}.py`, `src/harness/drivers/{fake_forge,github_client,
github_forge,memory}.py`, `src/harness/ports/forge.py`, `src/harness/projection.py`,
and their corresponding test files.

## What the conflict actually was

Both branches independently built a **self-healing / auto-issue-filing feature for
failed tasks**, diverging from the same merge-base (`0c8027b`):

- **`origin/main`** (already merged via PR #42 and follow-ups): a standalone
  `Healer` core loop (`src/harness/healer.py`) that claims tasks directly out of
  `failed/`, runs the `healer` persona, and files an issue through a new
  `IssueTracker` port (`ports/issues.py`, `drivers/github_issues.py`), settling to
  a new terminal queue `healed/`. This design is already fully documented as
  invariants 24–27 in `CLAUDE.md` (which merged with zero conflict, confirming it's
  the accepted design) and ships with its own unit/e2e tests
  (`test_healer.py`, `test_self_heal_e2e.py`). Main also shipped an unrelated but
  overlapping generalization of `build()`/`BoardProjection` to serve N workflows
  or none (FR-6/FR-7), and a `resolver` workflow + `ResolveConflictBehavior` (the
  very step that produced this artifact) plus mergeability watching.
- **This branch** (`harness/tsk_e0a23798942943d7`, 5 commits, all payload):
  a competing, task-pipeline-based design — a new `Forge.open_issue`/`FiledIssue`
  verb, `GithubClient.create_issue`/`find_issue` (different signature/return type
  than main's), a `FailedQueueTaskSource` that re-queues failed tasks as a second
  `healer` workflow (`diagnose` → `file_issue` steps), `Outcome.BUG_CONFIRMED`/
  `NOT_A_BUG`, and a narrow `active_workflows`/`StepCollisionError` mechanism to
  run two workflows in one process.

These are not reconcilable as two coexisting features: they solve the identical
problem (what happens to a task in `failed/`) by two incompatible mechanisms, and
literally collide on `GithubClient.create_issue` (same method name, different
signature/return type on the same ABC). Running both would also violate the
already-established invariant "`failed/` has one reader — the healer" (both
`Healer.tick()`'s claim and `FailedQueueTaskSource.poll()`'s list would read
`failed/`).

## Resolution

Adopted `origin/main`'s design as authoritative for every overlapping piece (it's
already shipped, invariant-documented, and tested) and dropped this branch's
superseded implementation:

- `ports/forge.py`, `fake_forge.py`, `github_forge.py`: kept `PullRequestState`/
  `pull_request_state` (main); dropped `FiledIssue`/`open_issue`.
- `github_client.py`, `memory.py`: kept main's `IssueTracker`-oriented
  `create_issue(..., labels=)`/`search_issue_by_marker`; dropped this branch's
  `IssueRef`/`find_issue`/`create_issue(title, body)`.
- `app.py`, `cli.py`: kept main's generalized `build(root, workflows=...)`,
  `ServedWorkflowRepository`, `HealConfig`, `RESOLVE_STEP`/`ResolveConflictBehavior`,
  `--heal-repo`/`--watch-mergeability`/`--resolver-workflow` CLI wiring; dropped
  this branch's `workflow_name`-based `active_workflows`/`StepCollisionError`,
  `heal`/`healer_repo`/`healer_workflow_name` params, `--heal`/`--healer-repo`
  flags, and the `_DIAGNOSE_PERSONA`/`diagnose` agent entry.
- `projection.py`: kept main's `column_order(steps, workflows, healed=)` +
  `BoardProjection(steps, workflows, include_healed=)` (supports N/0 workflows);
  dropped this branch's narrower `column_order(*workflows)` variant.
- `models.py`: dropped `Outcome.BUG_CONFIRMED`/`NOT_A_BUG` (only consumer was the
  deleted e2e test below).
- Deleted the now-dead files implementing the superseded design:
  `src/harness/behaviors/file_issue.py`, `src/harness/drivers/failed_queue_source.py`,
  `tests/test_file_issue_behavior.py`, `tests/test_failed_queue_source.py`,
  `tests/test_healer_e2e.py` (superseded by main's `tests/test_self_heal_e2e.py`,
  which already covers the equivalent scenarios).
- Test files (`test_app.py`, `test_cli.py`, `test_fake_forge.py`, `test_forge_memory.py`,
  `test_github_client.py`, `test_github_forge.py`, `test_projection.py`): resolved
  to main's versions, since they exercise the design that survived.

Every file listed above now matches `origin/main` byte-for-byte (verified with
`diff`), except `app.py` and `cli.py`, which additionally carry small pre-existing,
compatible cleanups (removed two dead branch-specific validation lines and reverted
one now-unused generalized helper parameter back to main's simpler form).

## Verification

No `<<<<<<<`/`=======`/`>>>>>>>` markers remain anywhere in the tree (checked
recursively, excluding `.venv`). Full test suite:

```
.venv/bin/pytest -q
950 passed, 1 skipped, 1 warning in 35.09s
```
