# Development: serve multiple workflows in a single running harness

Implements plan-01/design-01/architecture-01 as written, including all three
architecture refinements (R1 dedupe, R2 empty `--all-workflows` startup error,
R3 `--github-workflow` membership check).

## What changed

**`src/harness/ports/workflows.py`**
- Added `WorkflowRepository.names() -> tuple[str, ...]` as a new abstract
  method (lenient enumeration, strict resolution — mirrors
  `RepositoryRegistry.names()`).

**`src/harness/drivers/fs_workflows.py`**
- `FilesystemWorkflowRepository.names()`: globs `<root>/*.json`, returns
  sorted stems; `()` if the root doesn't exist. Broken files still show up
  here (only `get()` validates).
- New `ServedWorkflowRepository(inner, names)` decorator: `get()` raises
  `WorkflowNotFound` for any name outside the served set, with a message
  naming the unserved workflow and what *is* served
  (`"workflow 'other' is not served by this harness (served: default, hotfix)"`).
  Applies R1: `_served = tuple(dict.fromkeys(names))`, so a duplicated name in
  the input never shows up twice in the message.

**`src/harness/drivers/memory.py`**
- Added `MemoryWorkflowRepository.names()` — this implementation wasn't
  mentioned in the design docs but is a real `WorkflowRepository` used by
  several existing tests; the ABC's new abstract method made it
  uninstantiable without this.

**`src/harness/projection.py`**
- Extracted the existing single-workflow BFS into `_reachable_order(workflow)`.
- `column_order` now takes `Sequence[Workflow]` and unions each workflow's
  reachable steps, first-seen order wins (workflow[0]'s steps first, then
  workflow[1]'s not-yet-seen steps, ...).
- `BoardProjection.__init__` takes `Sequence[Workflow]` instead of one
  `Workflow`. Everything below construction (`hydrate`/`apply`/`snapshot`/...)
  is unchanged — it only ever read `self._order`.

**`src/harness/app.py`**
- `Harness.workflow: Workflow` → `Harness.workflows: dict[str, Workflow]`
  (name → resolved workflow, insertion-ordered = served order).
- The `started` event's payload: `workflow=<name>` → `workflows=sorted(...)`.
- `build()`'s second positional parameter widens from `workflow_name: str` to
  `workflows: str | Sequence[str]`. A bare string still hits the same
  single-workflow path as before (`names = (workflows,) if isinstance(...)`).
- Resolves every served name via the *raw* `FilesystemWorkflowRepository`
  (fail-fast, same error path as today), builds `step_queues` as the union of
  every resolved workflow's steps (a plain dict comprehension de-dupes by
  step name automatically), then wraps the raw repository in
  `ServedWorkflowRepository(raw, tuple(resolved))` and hands **only** the
  wrapped one to `Dispatcher` — the raw one never leaves `build()`.
  `BoardProjection` gets the list of resolved `Workflow` objects, in served
  order.

**`src/harness/cli.py`** (`run` subcommand only; `init`/`submit` untouched)
- `--workflow` is now repeatable (`action="append"`, `dest="workflows"`);
  unset defaults to `["default"]`, identical to before.
- New `--all-workflows` flag, mutually exclusive with `--workflow` (manual
  `if`/`print`/`return 2`, matching this file's existing validation style —
  no `argparse` mutual-exclusion group is used anywhere else in the file).
- New `_resolve_served_workflows(args, layout) -> tuple[str, ...] | None`:
  resolves the served name list, or prints an error and returns `None` on
  mutual-exclusivity violation or an empty `--all-workflows` discovery (R2).
- `_run()` now calls `_resolve_served_workflows` first, then validates
  `args.github_workflow` is in the served set before building GitHub sources
  (R3), then passes the served-name tuple to `build()`.
- `_init`'s two `harness.workflow` reads become `harness.workflows[args.workflow]`.

## Tests added

- `tests/test_fs_workflows.py`: `names()` (sorted stems, missing root, lenient
  on broken files) and `ServedWorkflowRepository` (serves only the given
  names, message shape, R1 dedup, `names()` returns the served set not the
  inner's).
- `tests/test_projection.py`: `column_order`/`BoardProjection` union across
  two workflows sharing steps, no duplicate columns.
- `tests/test_app.py`: `build()` with two workflow names unions step queues
  into one consumer per distinct step; two served workflows (sharing `plan`/
  `review`) each route their own tasks to completion through the shared
  queues; a task for a valid-but-unserved workflow fails with the new
  "not served" message (not the generic "no queue" one); a task for a
  genuinely nonexistent workflow still fails as before.
- `tests/test_cli.py`: repeated `--workflow` serves exactly those names; no
  flag still serves only `default`; `--all-workflows` serves every definition
  found; `--workflow`+`--all-workflows` together is rejected;
  `--all-workflows` against an empty workflows directory is a startup error;
  `--github-workflow` naming an unserved workflow is rejected.
- Mechanical updates: ~23 existing `BoardProjection(WORKFLOW)` call sites →
  `BoardProjection([WORKFLOW])` across `test_projection.py`,
  `test_projection_events.py`, `test_api_sse.py`, `test_cli.py`; the one
  hand-built `started` fixture event in `test_projection_events.py` updated to
  the plural shape; `test_app.py`'s one `harness.workflow` read updated to
  `harness.workflows["default"]`.

## Verification

```sh
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Full suite: **490 passed, 1 skipped** (the skip is the opt-in
`HARNESS_SMOKE_CLAUDE=1` test, unaffected by this change). This includes
`tests/test_architecture.py` (all structural invariants still hold — no new
`drivers/` import in `dispatcher.py`/`consumer.py`/`projection.py`) and the
real-FS/git smoke tests (`test_smoke.py`, `test_smoke_git.py`,
`test_smoke_github.py`), plus the phase 2/3/4 e2e suites.

No `.venv` existed in the checkout beforehand — created it per this repo's
documented dev setup; it's git-ignored.

## Notes / deviations from the artifacts

- One gap the design/architecture docs didn't call out: `drivers/memory.py`'s
  `MemoryWorkflowRepository` (used by `test_dispatcher.py` and
  `test_memory_drivers.py`) is also a `WorkflowRepository` implementation and
  needed `names()` added, or the new abstract method would make it
  uninstantiable. One-line fix, no behavior change to existing tests.
- Everything else matches design-01/architecture-01 exactly: resolution order
  is preserved end-to-end (CLI flag order → `build()`'s `resolved` dict →
  `BoardProjection`'s column union), no `set()` was introduced anywhere in
  that path, and `--all-workflows` uses `names()`'s sorted order per FR-4.
