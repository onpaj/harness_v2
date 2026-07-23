# Review — per-workflow board tabs

Reviewed `development-01.md`'s implementation (commit `dfdcb4e`) against
`plan-01.md` (FR-1..FR-7), `design-01.md`'s concrete shapes, and
`architecture-01.md`'s one required fix and risk list.

## Conformance check

- **FR-1 (enumerate definitions)** — `WorkflowRepository.names()` added as
  abstract; `FilesystemWorkflowRepository.names()` globs `*.json`, reuses
  `get()`'s own validation via `try/except WorkflowNotFound`, sorts.
  `MemoryWorkflowRepository.names()` returns `sorted(self._workflows)`.
  Verified with `test_names_lists_valid_definitions_sorted_and_skips_broken`,
  `test_names_missing_root_is_empty`, and a cross-implementation ordering
  check (`test_fs_and_memory_names_agree_on_ordering`). Matches design exactly.
- **FR-2 (workflow-aware `Board`)** — `BoardTab` added (`name`, `columns`,
  `.column()`, `.to_dict()`); `Board.columns` replaced by
  `Board.workflows: tuple[BoardTab, ...]`; `UNKNOWN_WORKFLOW = "unknown"`
  constant added next to the existing column constants. `Board.column()` is
  removed as design specified (no compatibility shim).
- **FR-3 (tasks land in the tab matching their template)** — `_store` resolves
  `tab = task.workflow_template if ... else UNKNOWN_WORKFLOW`, keyed
  independently of which queue/column delivered the event. Verified by
  `test_tasks_land_in_the_tab_matching_their_own_template` and
  `test_same_step_name_in_two_workflows_stays_isolated` (two workflows sharing
  a step name `plan` stay isolated).
- **FR-4 (orphaned templates never silently dropped)** — the architecture
  review's required fix is present verbatim:
  `self._orders[UNKNOWN_WORKFLOW] = (TODO_COLUMN, DONE_COLUMN, FAILED_COLUMN)`
  in `projection.py`, with the reasoning captured in a comment. The specific
  regression test the architecture doc asked for exists and passes:
  `test_hydrate_puts_unrecognized_template_inbox_task_in_unknown_todo`. The
  empty-tab omission is also covered
  (`test_unknown_tab_is_omitted_when_empty`), and end-to-end via
  `test_board_e2e.py::test_failed_task_lands_in_failed_column` /
  `test_restart_moves_failed_task_back_to_todo`, both updated to assert against
  the `unknown` tab specifically.
- **FR-5 (tab strip, survives SSE)** — `board.html` carries
  `data-active-workflow` on `#board` (untouched by `hx-swap="innerHTML"`,
  which only replaces children), `applyActiveTab()` toggles `.tab.active` /
  `.workflow-panel` display, click delegate updates the attribute client-side
  (no request), and the existing `htmx:afterSwap` listener gained a
  `target.id === 'board'` branch that re-applies the active tab after a
  fragment swap — exactly the mechanism the design specified, folded into the
  existing listener rather than a second one.
- **FR-6 (deterministic default tab)** — `Board.default_tab()`: `"default"` if
  present, else first alphabetically, else `None`; lives once on `Board`, used
  by both the server-rendered initial attribute and nowhere reimplemented in
  routes/templates. `names()` and `snapshot()`'s tab ordering are both
  alphabetical (`sorted()` / `tabs.sort()`). Verified by
  `test_snapshot_tabs_are_sorted_alphabetically` and
  `test_index_marks_default_tab_active_via_data_attribute`.
- **FR-7 (JSON/HTML lock-step)** — `routes.py` unchanged at the call-site level
  (`view.snapshot().to_dict()` / `{"board": view.snapshot()}`); both surfaces
  render from the same `Board` object structurally, not by convention.

## Architecture / invariants

- Invariant 5 (`projection.py`/`api/` import nothing from `drivers/`) and the
  "only `app.py`/`cli.py` wire drivers" rule both hold —
  `tests/test_architecture.py` (14 tests) passes unchanged, no new cases
  needed since the existing tests already scan file-by-file.
- Invariant 7 (`task` + `queue` on every board-mutating event) holds:
  `BoardProjection.apply(self, column: str, task: Task) -> None` signature is
  byte-for-byte unchanged, so `ProjectionSink.emit` needed no edit — tab
  resolution is correctly internal to `_store`, not leaked to the event
  producer, matching architecture-01.md's explicit contract-to-hold.
- `app.py build()` wiring matches the design's amendment: keeps the single
  active `workflow` for `step_queues`/dispatch, separately discovers every
  workflow via `names()` (skipping any that fails `get()` between the two
  calls), and defensively appends the active workflow if `names()` didn't
  surface it. `test_build_gives_every_discovered_workflow_a_board_tab`
  confirms both halves: board tabs include `{"default", "hotfix"}` while
  `step_queues` stays scoped to `{"plan", "review"}` only.
- Test blast radius from architecture-01.md's Risk 2 (`Board(`, `BoardColumn(`,
  `.column(`, `BoardProjection(` call sites beyond the plan's six named files)
  was fully covered: `test_api_artifacts.py`, `test_api_sse.py`, `test_app.py`,
  `test_cli.py` are all present in the diff alongside the originally-named
  files.

## Correctness

- Ran `.venv/bin/pytest -q`: **488 passed, 1 skipped** (the opt-in
  `HARNESS_SMOKE_CLAUDE` test), matching development-01.md's reported result.
  `test_architecture.py` (14/14) passes in isolation.
- Manually traced `_store`/`snapshot`/`hydrate` logic against the FR-3/FR-4
  acceptance criteria in plan-01.md; no discrepancy found between the written
  design and the shipped code — the diff is essentially a direct translation
  of design-01.md's pseudocode, plus the one architecture-mandated fix.
- No dead code, no stray `TODO`/half-finished branches. `BoardTab.column()` /
  `Board.workflow()` remain linear scans over small tuples as both the design
  and architecture review deliberately chose (not worth a dict at single-digit
  tab/column counts).
- Templates: `_columns.html`'s `.card` block is a byte-for-byte carry-over
  from the pre-tabs markup, just nested one level under `.workflow-panel`, as
  specified — no behavioral change to task cards themselves.

## Verdict rationale

Every functional requirement (FR-1..FR-7) is implemented and has a
corresponding passing test. The one required fix from architecture-01.md
(the `unknown` tab must include `todo`) is present and specifically
regression-tested. Invariants 1, 4, 5, 7 all hold, verified by the existing
architecture test suite with no gaps. No correctness bugs, no missing error
handling at a boundary that matters, no scope creep. Nothing here rises to a
`request_changes`-worthy issue.
