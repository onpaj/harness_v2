# Development — per-workflow board tabs

Implements `plan-01.md` / `design-01.md` as confirmed by `architecture-01.md`,
including the one required fix it flagged (the `unknown` tab's column set must
include `todo`, not just `done`/`failed`).

## Summary

One tab per discovered workflow definition now renders on the board, each
showing that workflow's own step columns. Tasks are grouped into the tab
matching their own `task.workflow_template`, never the queue that happened to
deliver the event. A task whose template names no discovered workflow lands in
a reserved `unknown` tab (only shown when non-empty). Tab switching is
client-side only and survives the periodic SSE-driven board refresh. This is a
read-side-only change — `router.py`, `dispatcher.py`, `consumer.py` are
untouched; dispatch still runs against the single active `--workflow`.

## Files changed

**Ports / core**
- `src/harness/ports/workflows.py` — `WorkflowRepository` gains abstract
  `names() -> list[str]` (lenient enumeration next to strict `get()`).
- `src/harness/ports/board.py` — new `BoardTab` (name, columns, `.column()`,
  `.to_dict()`) and `UNKNOWN_WORKFLOW = "unknown"` constant. `Board` changes
  from `columns: tuple[BoardColumn, ...]` to `workflows: tuple[BoardTab, ...]`,
  gains `.workflow(name)` and `.default_tab()` ("default" if present, else
  first alphabetically, else `None`). `Board.column()` is removed — a flat
  lookup no longer makes sense once the same step name can exist in two tabs.
- `src/harness/projection.py` — `BoardProjection.__init__` now takes
  `Sequence[Workflow]` instead of one `Workflow`; precomputes each workflow's
  `column_order()` into `self._orders[name]`, plus
  `self._orders[UNKNOWN_WORKFLOW] = (TODO_COLUMN, DONE_COLUMN, FAILED_COLUMN)`
  — the architecture review's required fix, so a task with an unrecognized
  template sitting in the inbox at `hydrate()` time isn't silently dropped
  before the dispatcher gets a chance to fail it. `_store` now resolves the
  tab from `task.workflow_template` (falling back to `UNKNOWN_WORKFLOW`);
  `apply(column, task)`'s external signature is unchanged, so
  `ProjectionSink.emit` needed no edit. `snapshot()` builds one `BoardTab` per
  order, omitting the `unknown` tab when it holds no tasks, then sorts tabs
  alphabetically.

**Drivers**
- `src/harness/drivers/fs_workflows.py` —
  `FilesystemWorkflowRepository.names()`: globs `<root>/*.json`, keeps a stem
  only if `get()` (already-existing validation) accepts it; missing root →
  `[]` (mirrors `RepositoryRegistry.names()`'s contract).
- `src/harness/drivers/memory.py` — `MemoryWorkflowRepository.names()`:
  `sorted(self._workflows)`.

**Wiring**
- `src/harness/app.py` `build()` — keeps `workflow = workflows.get(workflow_name)`
  driving dispatch/`step_queues` unchanged; additionally discovers every
  workflow via `workflows.names()` (skipping any that fails `get()` between
  the two calls) and always includes the active workflow even if it wasn't
  independently discoverable, then constructs
  `BoardProjection(discovered)`.

**Templates**
- `src/harness/api/templates/board.html` — `#board` gains
  `data-active-workflow="{{ board.default_tab() or '' }}"`, which
  `hx-swap="innerHTML"` never touches (it replaces only `#board`'s children).
  Added `applyActiveTab()` (toggles `.tab.active` / `.workflow-panel` display),
  a click delegate on `#board` for tab switching (no request sent), and an
  `htmx:afterSwap` branch (folded into the existing modal listener) that
  re-applies the active tab after every SSE-triggered fragment swap. CSS for
  `.tab-strip` / `.tab` added.
- `src/harness/api/templates/_columns.html` — restructured into a tab strip
  (rendered only when `board.workflows|length > 1`) plus one `.workflow-panel`
  per tab, each wrapping the pre-existing `.column`/`.card` markup unchanged
  (just nested one level deeper). Inactive panels stay in the DOM
  (`display: none`), not removed, so an SSE refresh on a non-default tab keeps
  working without losing client state.

## Interface changes

`GET /api/board` JSON shape changed (documented as an internal, non-frozen
contract in the plan):
```
Before: {"revision": N, "columns": [...]}
After:  {"revision": N, "workflows": [{"name": "default", "columns": [...]}, ...]}
```
`GET /fragment/board` HTML gained the tab strip; no route signatures changed
(`board()`/`fragment_board()` still just call `view.snapshot()`), so JSON and
HTML stay in lock-step by construction (FR-7).

## Tests

Updated to the new shapes and added new coverage:
- `tests/test_board_port.py` — `BoardTab.column()`, `Board.workflow()`,
  `Board.default_tab()` (all three branches), camelCase serialization of the
  new nested shape.
- `tests/test_projection.py` — all existing cases ported to
  `BoardProjection([WORKFLOW])` / `.workflow("default").column(...)`; new
  cases: tasks land in the tab matching their own template, same step name in
  two workflows stays isolated, unrecognized template falls back to `unknown`,
  `unknown` tab omitted when empty, **`hydrate()` places an unrecognized-
  template inbox task in `unknown` → `todo`** (the architecture review's Risk
  1 regression test), tabs sorted alphabetically regardless of construction
  order.
- `tests/test_fs_workflows.py` — `names()` lists valid definitions sorted and
  skips a broken one, missing root → `[]`, and a cross-implementation
  consistency check that `FilesystemWorkflowRepository.names()` and
  `MemoryWorkflowRepository.names()` agree on ordering for the same names
  (per the architecture doc's explicit ask).
- `tests/test_memory_drivers.py` — `MemoryWorkflowRepository.names()`.
- `tests/test_app.py` — `build()` gives every discovered workflow a board tab
  while `step_queues` stays scoped to the single active workflow.
- `tests/test_api_html.py` — single workflow renders no tab strip; multiple
  workflows render one `.tab`/`.workflow-panel` per workflow, alphabetically
  ordered; `index` marks the default tab active via `data-active-workflow`.
- `tests/test_api_json.py`, `tests/test_api_artifacts.py`,
  `tests/test_api_sse.py`, `tests/test_board_e2e.py`, `tests/test_cli.py`,
  `tests/test_projection_events.py` — mechanical updates to the new
  `Board(workflows=(BoardTab(...),))` / `BoardProjection([...])` /
  `.workflow(name).column(...)` shapes. `test_board_e2e.py`'s
  `test_failed_task_lands_in_failed_column` and
  `test_restart_moves_failed_task_back_to_todo` now assert against the
  `unknown` tab specifically, since their task's `workflow_template="neznamy"`
  is genuinely undiscoverable in that test's single-`default.json` setup —
  this exercises the same `hydrate()`-before-dispatch path as the new
  regression test above, end-to-end through the real dispatcher.

## How to verify

```sh
.venv/bin/pytest -q
```
488 passed, 1 skipped (the opt-in `HARNESS_SMOKE_CLAUDE` test). Includes the
real-FS `test_smoke.py` and real-git `test_smoke_git.py`.

Manual smoke (also run by hand during this session): built a harness with
`default.json` and `hotfix.json` both present, one task under each
`workflow_template`, hydrated from disk, and inspected both `GET /` and
`GET /api/board` — confirmed two tabs render (`data-workflow="default"` and
`"hotfix"`), `data-active-workflow="default"` is set on first load, and each
task appears only inside its own tab's `todo` column in the JSON response,
never the other tab's.

## Scope notes (unchanged from plan/design)

- Concurrent execution of multiple workflows (shared step queues across
  definitions, per-repo GitHub workflow selection) is out of scope — only the
  active `--workflow` has live step queues/consumers; other tabs show no
  in-flight tasks beyond `done`/`failed`/`unknown` until a separate effort.
- Discovery is a one-time scan at `app.py build()` — a workflow file added
  while the harness is running needs a restart to appear as a tab.
- Tab selection is client-side only, not URL-addressable.
