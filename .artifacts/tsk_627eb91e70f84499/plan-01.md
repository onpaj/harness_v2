# Per-workflow tabs on the board UI

## Summary

The board currently renders columns for exactly one workflow — the one passed to
`harness run --workflow`, hardcoded into a single `BoardProjection`. The board-ui
design doc explicitly left this out ("Multiple workflows... A switcher arrives
once more workflows do"). This task builds that switcher: one tab per workflow
*definition* found on disk, each showing its own step columns, with tasks
grouped into the tab that matches their own `workflow_template`.

## Context

Multiple workflow definition files can already coexist under `<root>/workflows/`
(`harness init --workflow <name>` writes a new `<name>.json` without touching an
existing one), and a task's `workflow_template` is already looked up per-task by
the dispatcher (`Dispatcher.tick()` calls `self._workflows.get(task.workflow_template)`
for every task, not once at startup). What is missing is purely on the read side:
`BoardProjection` is constructed with a single `Workflow` and computes one global
column order, so today a second workflow definition has no visible representation
on the board at all — its tasks either don't appear or are misplaced against the
active workflow's columns.

Non-goal, called out explicitly: making the harness actually *run* several
workflows side by side (shared step queues/consumers keyed off the union of every
definition's steps, `--github-workflow` per repository, hot-reloading a
definition added after startup) is a materially bigger change to `app.py`'s
wiring and is not required to satisfy "one tab per workflow definition, each
showing that workflow's own columns" — that sentence is a statement about the
board, not about the dispatcher. It's flagged under Open questions in case the
later design step disagrees.

## Functional requirements

**FR-1 — Enumerate workflow definitions.**
`WorkflowRepository` gains `names() -> list[str]`, mirroring the existing
`RepositoryRegistry.names()` (ports/repos.py): lenient enumeration next to a
strict `get()`. `FilesystemWorkflowRepository.names()` lists `<root>/*.json`,
returns each valid stem; a file that fails to parse (bad JSON, missing `start`,
malformed transition) is skipped from the list rather than raising —
enumeration must survive one broken definition. `MemoryWorkflowRepository.names()`
returns its dict's keys.
- Acceptance: root with `default.json`, `hotfix.json`, and a `broken.json`
  containing invalid JSON → `names()` returns `["default", "hotfix"]` (order
  per FR-6), `broken` silently omitted.

**FR-2 — Workflow-aware `Board`.**
`ports/board.py` gains a tab type (`BoardTab`: `name`, `columns`) sitting
alongside the existing `BoardColumn`. `Board` changes from `columns` to
`workflows: tuple[BoardTab, ...]`; `revision` stays a single board-wide counter.
A reserved tab name, `UNKNOWN_WORKFLOW`, is added next to the existing
`TODO_COLUMN`/`DONE_COLUMN`/`FAILED_COLUMN` constants.
- Acceptance: `GET /api/board` returns
  `{"revision": N, "workflows": [{"name": "default", "columns": [...]}, {"name": "hotfix", "columns": [...]}]}`.

**FR-3 — Tasks land in the tab matching their own template.**
`BoardProjection` is constructed from a list of `Workflow`s (one per discovered
definition) instead of one. It precomputes each workflow's `column_order()` as
its tab's column set. Both `hydrate()` and `apply()` resolve a task's tab from
`task.workflow_template`, not from whichever queue happened to deliver it.
- Acceptance: two workflows that both happen to define a step called `plan`,
  each with one task sitting in it → `snapshot()` shows each task only inside
  its own tab's `plan` column, never the other's.

**FR-4 — Orphaned templates are never silently dropped.**
A task whose `workflow_template` names no discovered definition (a workflow
file deleted after the task was created, or a genuinely unknown name) is placed
in the reserved `UNKNOWN_WORKFLOW` tab instead of vanishing from the board. That
tab only carries `done`/`failed` columns — a task can only end up with an
unrecognized template via a dispatch failure (`Dispatcher._fail` on
`WorkflowNotFound`) or historical `done/failed` data from a definition removed
later; it can never be legitimately "in progress" under a template nobody
recognizes, since the dispatcher fails it before ever assigning a step column.
This generalizes the existing board-ui completion check ("a task with an
unknown workflowTemplate appears in the Failed column, reason included") from
one workflow to N.
- Acceptance: a task with `workflow_template="ghost"` (no `ghost.json` on disk)
  that reached `failed/` appears under tab `"unknown"` → column `failed`, with
  its history's `reason` intact.
- Acceptance: the `unknown` tab is omitted from the rendered tab strip when it
  holds zero tasks, so a normal run never shows an empty extra tab.

**FR-5 — Tab strip in the HTML board.**
`board.html`/`_columns.html` render a tab per workflow (label = its name) plus
that workflow's columns as a panel; only the active tab's panel is visible.
Switching tabs is a client-side toggle — no server round trip — so it survives
the periodic SSE-triggered `/fragment/board` refresh: the active tab selection
lives in a `data-active-workflow` attribute on the outer `#board` element (which
htmx's `hx-swap="innerHTML"` never touches, since it replaces only the
children), and an `htmx:afterSwap` handler re-applies the active tab's
visibility to the freshly swapped-in panels — the same pattern already used for
revision de-duplication in `board.html`.
- Acceptance: click the `hotfix` tab, then have a task move column via SSE →
  the board redraws but stays on the `hotfix` tab, showing the update.

**FR-6 — Deterministic default tab.**
On a fresh page load (no prior client-side selection) the active tab is
`"default"` if that workflow exists, else the first name alphabetically.
`names()`/tab ordering elsewhere is plain alphabetical.
- Acceptance: workflows `["hotfix", "default"]` on disk → tab strip renders
  `default, hotfix` in that order and `default` is active on first load.

**FR-7 — JSON API stays in lock-step with HTML.**
`/api/board`'s JSON and `/fragment/board`'s HTML are both built from the same
`Board` object (unchanged principle from the original board-ui design) — no
separate code path can drift the tab list or column contents between them.

## Non-functional requirements

- **No behavior change to dispatch.** `router.py`, `dispatcher.py`,
  `consumer.py` are untouched; this is additive to the read side only
  (`ports/board.py`, `projection.py`, `app.py` wiring, `api/`).
- **Invariants held.** `projection.py` and `api/` still import nothing from
  `drivers/` (invariant 5 / `test_architecture.py`); an event still carries
  both `task` and full `queue` (invariant 7) — unchanged, since tab resolution
  reads `task.workflow_template` off the task snapshot already on the event.
- **Startup cost stays O(workflows × steps)**, done once when `BoardProjection`
  is constructed — no periodic re-scan (see Open questions).
- **No new endpoints or SSE channels** — tab switching is client-only; the
  existing single `/api/events` revision stream still drives every tab's
  redraw.

## Data model

- `Workflow` (`models.py`, unchanged) — `name`, `start`, `transitions`.
- `Task.workflow_template` (unchanged) — now doubles as the tab key.
- New `BoardTab` (`ports/board.py`): frozen dataclass, `name: str`,
  `columns: tuple[BoardColumn, ...]`, `to_dict()` analogous to `BoardColumn`.
- `Board` (`ports/board.py`, changed): `revision: int`,
  `workflows: tuple[BoardTab, ...]`; gains `workflow(name) -> BoardTab | None`
  replacing today's flat `column(name)` (which no longer makes sense once the
  same step name can exist in two tabs).
- New reserved constant `UNKNOWN_WORKFLOW = "unknown"` next to `TODO_COLUMN`/
  `DONE_COLUMN`/`FAILED_COLUMN`.

## Interfaces

- `GET /api/board` — JSON shape changes from `{"revision", "columns"}` to
  `{"revision", "workflows": [{"name", "columns": [...]}]}`. Breaking, but this
  is an internal read model with no frozen external contract (the design doc
  calls the JSON branch "a testing surface and a possible second client", not a
  public API) — acceptable in this phase.
- `GET /fragment/board` — HTML fragment gains a tab strip; unchanged
  htmx wiring (`hx-trigger="sse:board"`, `hx-swap="innerHTML"` on `#board`).
- `GET /fragment/task/{id}`, `GET /api/tasks/{id}`, artifacts and stage-output
  endpoints — unchanged; the detail modal already shows `task.workflow_template`
  (`_task.html` line 10).
- No new routes. Tab selection is not part of the URL in this iteration (see
  Open questions).

## Dependencies and scope

Depends on: `ports/workflows.py`, `drivers/fs_workflows.py`, `drivers/memory.py`
(`MemoryWorkflowRepository`), `ports/board.py`, `projection.py`, `app.py`
(`build()`'s projection wiring), `api/templates/{board,_ columns}.html`.

Explicitly out of scope:
- Concurrently *running* multiple workflows (shared step queues/consumers keyed
  by the union of every definition's steps, per-repository workflow selection
  for GitHub ingestion, hot-reload of a definition added after startup). Only
  the currently active `--workflow` has live step queues/consumers; other
  definitions' tabs will show no in-flight tasks beyond `done`/`failed` until
  that separate effort lands.
- Any board write action (still a read-only observation deck — pre-existing
  scope boundary from the original board-ui design).
- URL-addressable tab state, tab reordering/pinning, per-tab task counts beyond
  what's naturally visible per column.
- Authentication/multi-user/view persistence (unchanged pre-existing exclusion).

## Rough plan

1. `ports/workflows.py`: add abstract `names() -> list[str]`.
2. `drivers/fs_workflows.py`: implement `names()` (glob `*.json`, skip files
   that fail the same validation `get()` already performs).
3. `drivers/memory.py`: implement `MemoryWorkflowRepository.names()`.
4. `ports/board.py`: add `BoardTab`, `UNKNOWN_WORKFLOW`; change `Board` to hold
   `workflows` and add `Board.workflow(name)`.
5. `projection.py`: change `BoardProjection.__init__` to take a sequence of
   `Workflow`s; precompute each tab's column order (plus the `unknown` tab's
   fixed `(done, failed)`); rewrite `_store`/`apply`/`hydrate`/`snapshot` to key
   state by `(tab_name, column)`, resolving `tab_name` from
   `task.workflow_template` (falling back to `UNKNOWN_WORKFLOW`).
6. `app.py` `build()`: keep `workflow = workflows.get(workflow_name)` for
   dispatch wiring unchanged; additionally build
   `BoardProjection([workflows.get(name) for name in workflows.names()])`,
   skipping any name that fails `get()` between the two calls.
7. `api/routes.py` + `templates/board.html` + `templates/_columns.html`:
   render the tab strip and per-tab panels; add the client-side active-tab
   toggle (extends the existing revision-dedup IIFE pattern) and the
   `htmx:afterSwap` re-apply hook.
8. Update tests to the new shape: `test_fs_workflows.py`,
   `test_memory_drivers.py`, `test_board_port.py`, `test_projection.py`,
   `test_api_json.py`, `test_api_html.py`, `test_board_e2e.py`; add new cases
   for multi-workflow discovery, per-template task isolation, the `unknown`
   fallback tab, and default-tab ordering.
9. Manual smoke: two workflow definitions in one root (e.g. via two
   `harness init --workflow` runs against the same `--root`), submit a task
   under each `--workflow`, run the harness, confirm two tabs, correct columns,
   and that a task never appears under the wrong tab.

## Open questions

- **Concurrent execution of multiple workflows** (shared queues, per-repo
  GitHub workflow selection) is treated as future work, not required for "one
  tab per workflow definition" — flagging for the design step to confirm this
  reading is correct before implementation starts.
- **Fallback tab contents**: defaulted to `(done, failed)` only for `unknown`,
  reasoning that a task can never legitimately sit "in progress" under an
  unrecognized template. If design disagrees, the fix is a one-line change to
  the precomputed column tuple.
- **Discovery cadence**: defaulted to a one-time scan at `BoardProjection`
  construction (i.e., at harness startup), matching today's behavior for the
  single active workflow — a definition added while the harness is running
  needs a restart to appear as a tab, same as changing the active workflow does
  today.
- **Tab-switch mechanism**: defaulted to a client-side-only toggle (no
  `?workflow=` query param, no new route) so switching is instant and
  independent of the SSE refresh cadence. A URL-addressable tab is left for a
  later iteration if deep-linking to a specific tab turns out to matter.
