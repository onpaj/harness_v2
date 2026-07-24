# Merge conflict resolution — PR #125

Merging the base branch produced conflicts in three files, all stemming from
two features landing independently on `build()`/`cli.py`/`serve()`: the
manual "Add issue" `IssueImport` wiring (this branch) and a `repository_registry`
parameter plus a declarative `CheckDefinition` for the `failed-tasks` action
(base branch). Both features are independent and additive, so every conflict
resolved by keeping both sides.

## `src/harness/app.py`

- `build()`'s keyword-only parameter list: kept both `issue_import_factory`
  (this branch) and `repository_registry` (base) — the latter is already used
  a few lines below (`known_repositories = ... repository_registry.names() ...`),
  confirming it must survive the merge.
- The `checks` dict's `"failed-tasks"` entry: took the base branch's
  `CheckDefinition(spec=FAILED_TASKS_SPEC, factory=...)` form (declarative
  spec for the process-admin UI), which fully subsumes this branch's plain
  lambda — no functionality lost, `FAILED_TASKS_SPEC` was already imported.

## `src/harness/cli.py`

- The `build(...)` call in `_run`: kept both `issue_import_factory=issue_import_factory`
  and `repository_registry=registry` as separate keyword arguments passed
  through to the now-merged `build()` signature.

## `tests/test_cli.py`

- Two conflicted `fake_serve` stubs: took the base branch's signature (with
  `registry: RepositoryRegistry | None = None`), matching the real `serve()`
  signature.
- Two further breakages the merge exposed (not marked as conflicts, but
  failing after resolution):
  - `test_run_passes_issue_import_factory_to_build_only_with_a_token`'s
    `fake_serve` was missing the `registry=None` parameter — added it.
  - `test_serve_wires_the_registry_into_the_filesystem_process_admin`'s
    `FakeHarness` was missing `self.issue_import = NullIssueImport()` (every
    other `FakeHarness` fixture in the file already sets it, since `serve()`
    now reads `harness.issue_import` unconditionally) — added it.

## Verification

Full suite: `1426 passed, 1 skipped` (the skip is the opt-in
`HARNESS_SMOKE_CLAUDE=1` real-`claude` test, expected to skip). No conflict
markers remain anywhere in the tree.
