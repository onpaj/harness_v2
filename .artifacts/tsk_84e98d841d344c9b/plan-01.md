# Plan: full-screen tabbed task detail view

## Summary

The task detail view is currently a small `<dialog>` (max-width 640px) that dumps
task info, history, artifacts and live output as one long stacked page
(`_task.html`). This redesigns it into a large, full-screen modal with three tabs —
**Task info**, **History**, **Output** — so each concern gets its own screen instead
of a long scroll. Pure front-end/template change: no new ports, no backend
data the harness doesn't already expose.

## Context

`src/harness/api/templates/_task.html` renders everything (metadata table, history
table, artifacts table, live SSE output) in one flow inside a small dialog
(`board.html:23`). As tasks accumulate history entries and artifacts, or as agent
output streams grow, this becomes a long scroll inside a cramped box. The board's
own styling (`board.html:9-26`) already establishes the visual language (Trello-like
cards/columns) this should stay consistent with.

Only the presentation layer changes. `BoardView`, `ArtifactView`, `StageOutputView`
and the routes that serve them are unchanged in shape — this is a `_task.html` +
`board.html` (CSS/JS) rework, plus perhaps splitting `_task.html` into
tab-fragment partials for clarity.

## Functional requirements

**FR-1 — Full-screen modal.**
The task detail dialog occupies the full (or near-full, e.g. 96vw × 92vh)
viewport instead of the current `max-width: 640px; width: 90%`.
- AC: opening any task card shows a modal that visually dominates the viewport
  on common desktop widths (≥1024px) and remains usable down to ~768px.
- AC: the existing open/close mechanics (click card → `hx-get
  /fragment/task/{id}` → swap into `#detail` → `showModal()`; click backdrop or
  close button → `close()` → innerHTML cleared to tear down the SSE connection,
  per the existing comment in `board.html:41-46`) keep working unchanged.

**FR-2 — Three tabs: Task info, History, Output.**
The detail view is split into three named tabs, replacing the current single
stacked layout.
- **Task info**: the existing metadata table (id, repository, worktree, workflow,
  status, lastOutcome, lockId, created, data) plus the artifacts table (currently
  a separate `<h3>artifacts</h3>` block) and the restart button for failed tasks.
- **History**: the existing history table (time/actor/from/to/outcome/reason),
  given its own full-height tab instead of a small embedded table.
- **Output**: the live SSE stage-output panel, given the full tab body height
  (currently constrained to `max-height: 240px`).
- AC: exactly one tab's content is visible at a time; clicking a tab header
  switches the visible panel without a network round-trip (client-side
  show/hide, no re-fetch of `/fragment/task/{id}`).
- AC: tab switching does not tear down the SSE output stream — switching away
  from and back to Output continues showing the live-streamed lines rather than
  reconnecting or losing history already rendered.

**FR-3 — Sensible default tab.**
- AC: opening a task defaults to the **Task info** tab.
- AC: (stretch, see Open Questions) if a task is currently running, consider
  defaulting to **Output** instead — deferred to an open question below.

**FR-4 — Tab state does not leak across tasks.**
- AC: opening task B after closing task A always starts back on the default tab
  (Task info), not wherever task A's tabs were left.

**FR-5 — Artifacts remain linkable.**
- AC: artifact links (`/api/tasks/{id}/artifacts/{step}/{attempt}/{name}`) keep
  working exactly as today, just relocated under the Task info tab.

**FR-6 — Restart action keeps working.**
- AC: the "Restart" button for `failed` tasks (`_task.html:2-6`) still posts to
  `/tasks/{id}/restart` and swaps the response into `#detail`; after restart the
  modal re-renders (tabs reset to default, per FR-4).

## Non-functional requirements

- **No new backend surface.** `BoardView`, `ArtifactView`, `StageOutputView` and
  their routes are unchanged (per CLAUDE.md invariant 5: neither `api/` nor
  `projection.py` may import `drivers/`; this task doesn't touch drivers at all).
- **htmx/SSE semantics preserved.** The existing SSE-lifecycle comment in
  `board.html:41-46` (clearing `#detail` innerHTML on close tears down the
  live-output `EventSource` via `htmx:beforeCleanupElement`) must keep holding
  — tab-switching must not accidentally clear/reinsert the SSE-connected div,
  since that would drop or duplicate output lines.
- **No new client-side framework/build step.** Stay with vanilla htmx + a small
  inline `<script>`, matching the project's current zero-build approach
  (`htmx.min.js`/`sse.js` vendored under `static/`).
- **Responsive down to ~768px** (tabs and full-screen modal shouldn't break on
  a laptop-sized window; mobile is out of scope, see below).
- **Escaping preserved.** Output lines are already HTML-escaped server-side
  (`routes.py:67`) — the redesign must not introduce a path that renders
  unescaped agent output.

## Data model

No new entities. The view consumes the same three things it does today:
- `Task` (via `BoardView.get`) — metadata + `history: list[HistoryEntry]`.
- `ArtifactRef` list (via `ArtifactView.list`) — step/attempt/name.
- Live output lines (via `StageOutputView.subscribe`, streamed over SSE).

## Interfaces

- `GET /fragment/task/{task_id}` — unchanged endpoint, but the returned HTML
  fragment (`_task.html`) is restructured internally into a tab-header row +
  three tab-panel `<div>`s (or, if it helps readability, split into
  `_task_tabs.html` including `_tab_info.html` / `_tab_history.html` /
  `_tab_output.html` partials — implementation detail for the dev step).
- `POST /tasks/{task_id}/restart` — unchanged, still returns the `_task.html`
  fragment which now re-renders with tabs reset.
- No new routes. Tab switching is pure client-side (CSS class toggle / `hidden`
  attribute driven by a small inline script or htmx `on click` attributes) —
  it should NOT be implemented as `hx-get` calls back to the server per tab,
  since that would risk re-establishing the SSE connection on every Output-tab
  click and would add latency/complexity for no benefit.

## Dependencies and scope

**Depends on:** existing `_task.html`, `board.html`, `_columns.html`,
`routes.py` (`build_html_router`), and the CSS embedded in `board.html`.

**In scope:** template/CSS/inline-JS rework of the detail modal; splitting
`_task.html` if that keeps the templates readable.

**Out of scope:**
- Any backend/port/driver change (no new data is needed for this redesign).
- Mobile-specific layout (project has no other mobile-optimized surfaces today).
- Persisting the last-selected tab across sessions/tasks.
- Changing what data is shown — this is a layout/navigation change, not a
  content change (aside from moving the artifacts table to live under Task
  info instead of its own top-level `<h3>`).
- Changing the board/column view (`_columns.html`, card layout) — untouched.

## Rough plan

1. **CSS**: enlarge `dialog#detail` to a full-screen layout (e.g.
   `width: 96vw; height: 92vh; max-width: none;` with internal flex layout:
   fixed header (task id + restart button + tab bar) + scrollable body).
2. **Template restructure**: rework `_task.html` into a tab-bar (`Task info` /
   `History` / `Output`) plus three panel containers holding the content that
   exists today, regrouped per FR-2. Keep the artifacts table under Task info.
3. **Tab switching script**: a small inline script (added once in `board.html`,
   scoped by event delegation off `#detail` similar to the existing close/clear
   handlers) that shows the clicked tab's panel and hides the others via a CSS
   class, defaulting to Task info on each fresh render (satisfies FR-3/FR-4
   for free, since the fragment is re-rendered from scratch on every open).
4. **Verify SSE survives tab switching**: confirm the Output panel's
   `hx-ext="sse"` div is not removed/re-added by the show/hide mechanism (use
   `display: none` / `hidden`, not conditional template rendering or
   `innerHTML` swap, so the SSE-connected element stays in the DOM).
5. **Manual verification**: run the app (`run` skill / existing dev flow),
   open a task in each of `todo`/working/`failed`/`done` states, click through
   all three tabs, confirm: restart button still works, artifact links still
   download, live output keeps streaming across tab switches, and closing the
   dialog still tears down the EventSource (no lingering connection in
   devtools' Network tab after close).
6. Existing automated tests: check whether any test renders/asserts on
   `_task.html` structure (e.g. smoke tests hitting `/fragment/task/{id}`) and
   update expectations for the new tab markup if so.

## Open questions

- **Default tab when a task is actively running**: should opening a currently-
  processing task default to the Output tab instead of Task info, so the user
  immediately sees live progress? Default assumed for this plan: always open
  on Task info for consistency/predictability; Output is one click away. Revisit
  if user feedback says otherwise.
- **Tab bar placement/style**: no existing tab pattern in this codebase to
  match. Default assumed: a simple horizontal row of buttons/links styled
  consistent with the existing `.badge`/`.card` visual language (light,
  Trello-esque), not a heavier UI-kit look.
- **Splitting `_task.html` into partials**: purely an implementation-detail
  choice for the dev step (single file with three `<div>`s vs. three included
  partials). Default assumed: keep it as a single `_task.html` unless it grows
  unwieldy, to minimize new files.
- **Existing test coverage on `_task.html` markup**: not yet confirmed whether
  any test parses this fragment's HTML structure directly (vs. just hitting the
  route and checking status code). The dev step should check
  `tests/` for this before assuming no test updates are needed.
