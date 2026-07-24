# Plan — Harness dashboard: local-timezone timestamps

## Summary
The board UI (`src/harness/api/templates/*.html`) currently renders every
timestamp — a task's `created`, each history entry's `at`, and the card's
"since" line — by dumping the raw string the harness stores on disk straight
into the page. That string is always UTC (`drivers/system_clock.py` formats
`datetime.now(timezone.utc)` as `%Y-%m-%dT%H:%M:%SZ`), so every viewer sees the
same UTC wall-clock regardless of where they are. This plan converts display
only: timestamps keep travelling as UTC ISO-8601 strings through the whole
backend (queues, projection, `BoardView`, JSON API) and are converted to the
viewer's local timezone entirely in the browser at render time.

## Context
The dashboard (`api/app.py`, `routes.py`, `templates/`) is server-rendered
Jinja + htmx, refreshed two ways: a full page load, and partial swaps pushed
by SSE (`/api/events` → `hx-get="/fragment/board"`, and
`/fragment/task/{id}` for the detail dialog). Any fix has to work for both,
not just the initial page load. Per invariant #5 in `CLAUDE.md`, `api/` and
`projection.py` must not know what the harness runs on and must stay
driver-agnostic; per general project layering, the backend has no legitimate
way to know a *browser's* timezone anyway (it isn't in scope of any port —
`Clock` reports the server's time). This makes the browser the only correct
place to do the conversion: no wire-format change, no new field on `Task`
or `HistoryEntry`, no risk to the on-disk queue format or the JSON API
consumed by anything else. The referenced branch
`claude/harness-dashboard-local-tz-wk764f` does not exist in this repository
(local or on `origin`) — treating this as new work, not a continuation.

## Functional requirements

**FR-1 — Timestamps render in the viewer's local timezone.**
Every timestamp currently shown raw — task detail "created"
(`_task.html:14`), history table "time" column (`_task.html:23`), and the
card's "since" line (`_columns.html:16`) — is displayed converted to the
browser's local timezone instead of the raw UTC string.
- Acceptance: with the OS/browser timezone set to e.g. `America/New_York`,
  a task created at `2026-07-23T18:00:00Z` shows a local wall-clock time
  (`2:00 PM` or equivalent), not `18:00`.
- Acceptance: changing the browser's timezone (devtools sensor override or
  OS setting) and reloading changes the displayed time accordingly — no
  server round-trip or setting needed.

**FR-2 — The timezone is visible, not just implied.**
Each rendered timestamp carries a visible timezone indicator (offset or
abbreviation, e.g. `GMT+2` or `CEST`), so a viewer comparing two tasks in
different browsers isn't misled into thinking both are the same zone.
- Acceptance: the rendered text includes a `timeZoneName` component (short
  form, e.g. via `Intl.DateTimeFormat` with `timeZoneName: "short"`).
- Acceptance: the full absolute UTC instant is still recoverable — kept as a
  `title` attribute or the `datetime` attribute of a `<time>` element — for
  anyone who needs to cross-check against server logs.

**FR-3 — Works after every dashboard refresh path, not just first load.**
The conversion re-applies after: (a) initial page load, (b) the SSE-driven
board refresh (`hx-get="/fragment/board"`), (c) opening a task's detail
dialog (`hx-get="/fragment/task/{id}"`), (d) the restart action's fragment
swap.
- Acceptance: after a live SSE update swaps in a new `_columns.html`
  fragment, newly rendered cards show local time immediately, with no stale
  raw-UTC flash beyond a single reflow.
- Acceptance: opening the detail dialog shows converted times without a
  manual refresh.

**FR-4 — DST and offset transitions are correct.**
Conversion must use real timezone rules (via the browser's `Intl`/`Date`
APIs), not a fixed offset, so a task that straddles a DST transition (e.g.
created before, viewed after the clocks change) still renders the wall-clock
time that was correct for its viewer's zone at that instant.
- Acceptance: a UTC instant just before and just after a documented DST
  transition in a DST-observing zone renders with the correct pre/post
  offset (verified via a couple of fixed instants in a unit-style JS test
  or manual devtools check), not a static offset baked in once per page
  load.

**FR-5 — Works in all major evergreen browsers.**
Uses only widely supported web platform APIs (`Date`, `Intl.DateTimeFormat`)
— no polyfill, no server-side user-agent sniffing.
- Acceptance: manual check (or automated if a browser test harness exists)
  in Chrome, Firefox, Safari shows equivalent local-time output for the
  same instant and same OS-level timezone.

## Non-functional requirements
- **No backend change to timestamp representation.** `Task.created`,
  `HistoryEntry.at`, the JSON API (`/api/board`, `/api/tasks/{id}`) all keep
  emitting UTC ISO-8601 strings unchanged — this is a pure presentation
  change, so nothing that reads the JSON API (scripts, other tooling)
  breaks.
- **No added network round-trip.** Conversion is synchronous, client-side,
  using data already present in the rendered HTML — no new endpoint, no
  extra fetch.
- **Negligible performance cost.** Formatting is O(number of visible
  timestamps) per swap — at most a few dozen DOM nodes on this board; no
  measurable jank.
- **Graceful degradation.** If `Intl.DateTimeFormat` somehow throws (e.g. an
  invalid/missing timestamp string), fall back to showing the raw UTC
  string rather than blanking the field or breaking the page.

## Data model
No data model changes. `Task.created: str` and `HistoryEntry.at: str` stay
UTC ISO-8601 (`%Y-%m-%dT%H:%M:%SZ`) end to end — in the queue JSON on disk,
in `Task.to_dict()` / `HistoryEntry.to_dict()`, in the `BoardView` snapshot,
and in the `/api/board` and `/api/tasks/{id}` JSON responses. Only the
Jinja templates change what they emit around that same string (a `<time>`
element carrying the raw value, rather than the raw value as bare text), and
a new small client-side script converts it for display.

## Interfaces
- **Templates touched:** `_columns.html` (card "since"), `_task.html`
  (detail "created" field and history table "time" column). Each raw
  `{{ ... }}` timestamp interpolation becomes a `<time datetime="{{ value }}"
  title="{{ value }} UTC">{{ value }}</time>` (server-rendered fallback
  content is the raw UTC string, replaced client-side).
- **New client-side script** (`api/static/`, loaded from `board.html`
  alongside `htmx.min.js`/`sse.js`): a small function that finds all
  `time[datetime]` elements in a given root, parses `datetime` with `new
  Date(...)`, and replaces the text content with
  `Intl.DateTimeFormat(undefined, {dateStyle: "medium", timeStyle: "medium",
  timeZoneName: "short"}).format(...)` (or equivalent). Invoked once on
  `DOMContentLoaded` for the initial server-rendered page, and again inside
  the existing `htmx:afterSwap` listener in `board.html:35` (which already
  fires on every fragment swap — board refresh, task detail, restart) so no
  new event wiring is needed beyond extending that one handler.
- **No changes to `routes.py`, `app.py`, `projection.py`, or any port.**
  This stays entirely inside `api/templates/` and `api/static/` — consistent
  with invariant #5 (`api/` doesn't need to know anything new about drivers)
  and invariant #11/#17 (no new port surface required).

## Dependencies and scope
- Depends on: nothing new. Reuses the existing `htmx:afterSwap` hook already
  present in `board.html` for revision de-dup bookkeeping.
- Out of scope: adding a user-facing timezone override/picker (browser's
  system timezone is authoritative, per the task's acceptance criteria).
  Out of scope: changing the on-disk or JSON timestamp format. Out of scope:
  localizing anything other than timestamp display (e.g. date/number
  formatting elsewhere). Out of scope: the non-existent
  `claude/harness-dashboard-local-tz-wk764f` branch — nothing to merge or
  reconcile from it.

## Rough plan
1. Add a small formatting helper in a new (or existing) static JS file:
   given a root element, find `time[datetime]` nodes and set their text to
   the `Intl.DateTimeFormat`-formatted local rendering of `datetime`; wrap in
   a try/catch that leaves the raw fallback text alone on failure.
2. Update `_columns.html` and `_task.html` to wrap each timestamp
   interpolation (`task.history[-1].at`, `task.created`, `entry.at`) in a
   `<time datetime="..." title="...">` element instead of bare text.
3. Wire the helper to run: once after `DOMContentLoaded` for the initial
   page, and inside the existing `htmx:afterSwap` listener in `board.html`
   for every subsequent fragment swap (board, task detail, restart).
4. Manually verify: initial load, live SSE board update, opening a task's
   detail dialog, and the restart flow all show local time with a visible
   zone indicator; spot-check a DST-transition instant and a non-DST zone.
5. Note in the PR description that the JSON API's timestamp format is
   unchanged (UTC ISO-8601) — this is a display-only change.

## Open questions
- **Exact display format** (`FR-2`) isn't specified beyond "clear... e.g.
  show '2:30 PM EST' or include timezone offset." Default chosen:
  `Intl.DateTimeFormat` with `dateStyle: "medium", timeStyle: "medium",
  timeZoneName: "short"` (renders like `Jul 23, 2026, 2:30:00 PM GMT+2`),
  with the exact UTC instant kept in the `title` tooltip. A later step can
  adjust the exact style without changing the approach.
- **Relative "since" styling on the card** (`_columns.html:16`, currently
  just the raw timestamp text) — the acceptance criteria only ask for local
  time, not a relative format ("3m ago"); this plan keeps it an absolute
  local timestamp for consistency with the detail view, not a relative
  string. If the actual UX intent was a relative/"time ago" display, that's
  a separate follow-up.
- The mentioned WIP branch `claude/harness-dashboard-local-tz-wk764f` could
  not be found (checked local branches and `origin`) — proceeding as if no
  prior work exists. If it turns up later (e.g. under a different remote),
  it should be diffed against this plan before continuing.
