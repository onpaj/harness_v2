# Plan — Mobile-first board and admin UI redesign

## Summary

Redesign `src/harness/api/` so the board and task-detail views are mobile-first
(stacked columns, full-screen detail sheet, bottom tab bar, dark mode, safe-area
insets) while the existing side-by-side kanban is preserved at `≥768px`. This is
a presentation-layer change only: no port, model, driver or route-behaviour
changes, aside from one new Jinja filter (`shorttime`).

## Context

The harness is operated mostly from an iPhone, but `board.html` / `_columns.html`
/ `_task.html` are built for desktop: columns scroll sideways, cards are dense,
and the detail view is a fixed-width `<dialog>` with cramped tables. This task
makes the phone the primary target while keeping desktop intact.

**Scope correction found during planning.** The request (and its acceptance
criteria) describe restyling an "Agents / Workflows" admin UI and preserving
"workflow tab switching" / "detail tab switching" JS hooks, and reference
`tests/test_api_agents.py` / `tests/test_api_workflows.py`. None of this exists
in the current tree: `src/harness/api/templates/` has only `board.html`,
`_columns.html`, `_task.html`; there is no `admin/` directory, no agent/workflow
admin routes or ports, and no such test files anywhere in `tests/`. `Board` is
also a flat set of step-columns (`ports/board.py`) with no grouping by
workflow — there is nothing to put "workflow tabs" over today. The reference
branch (`origin/claude/harness-mobile-ui-redesign-60sb0z`) that the task points
to as a guidance implementation is ~250 files and ~38k lines ahead of `main`
(self-healing, issue reconciliation, a docs site, and the admin CRUD UI all
landed there first) — it is useful for CSS/markup ideas but cannot be diffed or
merged wholesale; most of its non-UI content is out of scope for this repo's
current state.

Default taken (see Open questions): scope this task to the two surfaces that
exist today — the **board** (columns + cards) and the **task detail**
(info/history/artifacts/live-output). The nav is built as a small, data-driven
list of sections so that an Agents/Workflows admin UI can slot into it later
without touching the nav component again, but only a single "Board" entry is
wired for now. The "segmented Info/History/Output control" and "tab-strip"
markup are net-new UI (nothing to preserve there); "workflow tab switching"
and the admin restyling are dropped from this task's acceptance criteria.

## Functional requirements

**FR-1 — Shared stylesheet.**
All inline `<style>` blocks are extracted into one file,
`src/harness/api/static/app.css`, served via the existing `StaticFiles` mount
and linked with `<link rel="stylesheet" href="/static/app.css">` from every
template (`board.html`, and any partial rendered standalone). No external URLs,
fonts, or CDNs — `https://` must not appear anywhere in a rendered page body
(pinned by `test_index_renders_board_shell`). Design tokens (colors, spacing,
radii) are CSS custom properties on `:root`, with a full override block under
`@media (prefers-color-scheme: dark)`.
- *Acceptance:* `GET /` and `GET /fragment/board` responses contain
  `/static/app.css`; `python -c "import cssutils"`-free manual check that the
  file has no `@import url(https://...)`; `https://` absent from all rendered
  bodies.

**FR-2 — Responsive board layout.**
`.board` stacks columns vertically (`flex-direction: column`) below 768px and
switches to the existing side-by-side row (`flex-direction: row`, horizontal
scroll only as an overflow fallback for many columns) at `min-width: 768px`,
via one media query. No JS is involved in the layout switch.
- *Acceptance:* at a 390px viewport the board has no horizontal page scroll
  (`document.documentElement.scrollWidth <= innerWidth`, checked manually /
  by the verify pass, since no headless-browser test exists in this repo); at
  ≥768px columns sit side by side as today.

**FR-3 — Column header + empty state.**
Column headers become sticky within the column, keep the `<h2>{{ name }}
<span class="count">{{ n }}</span></h2>` structure (name and count still
present as plain text so existing/likely future text-based assertions keep
working), and an empty column collapses to a slim muted "No tasks" row instead
of reserving full card-height whitespace.
- *Acceptance:* `test_index_contains_columns` (checks column names appear)
  keeps passing; a column with `tasks=()` renders a muted placeholder, not an
  empty `<div class="column">…</div>` with no content markers.

**FR-4 — Card visual hierarchy.**
Cards keep the exact behavioural contract pinned in `tests/test_api_html.py`:
- title = `task.data.title` if set, else falls back to `task.id`
  (`.title` element, literal `>tsk_3<` must not appear when a title exists).
- `.repo` line renders `repository | basename` and `worktree | basename`
  joined with " · " only when at least one is set; never the raw path.
- `.badge.working` labelled "processing" appears only when `task.lock_id` is
  set.
- last-outcome badge (`.badge.{{ last_outcome }}`) appears only when set.
- `.since` timestamp appears only when `task.history` is non-empty, and shows
  the *last* history entry's `at`.
On top of that unchanged behaviour: a colored left border (`border-left`)
keyed off status/outcome (processing / done / request_changes / failed),
larger title type, pill-shaped badges, and the timestamp right-aligned in the
meta row using the new `shorttime` filter in the visible text while the full
ISO string stays available via a `title="{{ ... }}"` attribute on the same
element (so `test_card_shows_time_in_state_only_when_history_exists`, which
greps for the raw ISO string, keeps passing).
- *Acceptance:* all `test_card_*` assertions in `test_api_html.py` pass
  unchanged; visible time text is the short form; the ISO string is present in
  a `title`/`datetime` attribute on the same card.

**FR-5 — `shorttime` Jinja filter.**
`routes.py` registers a filter, e.g. `shorttime(value: str) -> str`, that
parses the stored ISO-8601 UTC string and renders a compact form
(`"Jul 22, 09:03"`). Unparseable input passes through unchanged (defensive,
matching the existing `_basename` filter's tolerance of `None`). This is the
one routes.py change allowed by the task's scope.
- *Acceptance:* a unit test (`tests/test_api_html.py` or a new
  `tests/test_shorttime_filter.py`) covers a normal timestamp, and confirms
  the full ISO string is still emitted in a `title` attribute wherever
  `shorttime` is used in a template.

**FR-6 — Bottom tab bar / top nav.**
A small nav partial (`_nav.html`) renders a single-item list of sections
(today: only "Board", `href="/"`). On the phone it is pinned to the bottom of
the viewport as an app-like tab bar with an inline SVG icon per item; at
`≥768px` it renders as inline links in a top app bar instead (same markup,
CSS-only repositioning — no JS layout switch). The active item is highlighted
client-side by comparing `location.pathname` against each link's `href`. The
list is data-driven (a Python list of `(label, href, icon)` passed from
`routes.py` or hard-coded in the partial as a loop) so a future Agents/Workflows
entry is a one-line addition, not a rewrite.
- *Acceptance:* nav renders on `/`; the "Board" item carries an
  `aria-current="page"` (or an `active` class) when `location.pathname` is
  `/`.

**FR-7 — Safe-area + iOS chrome.**
`<meta name="viewport" content="width=device-width, initial-scale=1,
viewport-fit=cover">`, `<meta name="theme-color" ...>` (light + dark via two
`media` variants), and `apple-mobile-web-app-capable` /
`apple-mobile-web-app-status-bar-style` meta tags are added to `board.html`'s
`<head>`. The nav bar and any other viewport-edge element pad with
`env(safe-area-inset-top/bottom/left/right)`.
- *Acceptance:* `viewport-fit=cover` present in the rendered `<head>`; nav bar
  CSS includes `env(safe-area-inset-bottom)`.

**FR-8 — Task detail as a sheet.**
`_task.html` (rendered into `#detail`) is restructured with a segmented
Info / History / Output control (three buttons + three panels, one visible at
a time, switched by inline `<script>` — new behaviour, added fresh):
- Info: the existing key/value rows (repository, worktree, workflow, status,
  lastOutcome, lockId, created, data), rendered as a stacked label-over-value
  list instead of a `<table>` on narrow screens (CSS `display: grid` /
  `flex-direction: column` per row; may keep a `<table>` fallback at desktop
  width, or use one grid-based markup that reads fine at both sizes — the
  design phase decides). Every optional field keeps its `or "—"` guard so no
  cell ever renders Python's `None`.
- History and Artifacts tables keep their exact column sets and `or "—"`
  guards, wrapped in their own `overflow-x: auto` container so only the table
  scrolls horizontally, never the page.
- Output panel keeps the live-output markup **byte-for-byte identical** in its
  hooks: `hx-ext="sse"`, `sse-connect="/api/tasks/{{ task.id }}/output/events"`,
  `sse-close="end"`, `id="stage-output"`, `sse-swap="line"`,
  `hx-swap="beforeend scroll:bottom"` — only its surrounding CSS classes
  change.
On the phone the whole fragment renders as a full-height slide-up sheet
(`<dialog>` styled to cover the viewport, or a plain fixed-position panel);
at `≥768px` the existing centered `<dialog>` behaviour is kept.
- *Acceptance:* `test_fragment_task_shows_metadata_and_history`,
  `test_fragment_task_unknown_returns_404`,
  `test_restart_button_shown_only_for_failed_task`,
  `test_restart_invokes_control_and_returns_refreshed_fragment`,
  `test_restart_returns_404_when_control_reports_nothing` all keep passing
  unmodified; `board.html`'s dialog show/close, close-on-backdrop-click, and
  "clear innerHTML on close" (SSE teardown) JS behaviour is unchanged;
  revision de-dup script is unchanged.

**FR-9 — Restart button.**
Kept exactly as-is functionally (`hx-post`, `hx-target="#detail"`,
`hx-swap="innerHTML"`, visible only when `task.status == "failed"`); only its
visual style (44px min tap target, pill/segmented look) changes.

## Non-functional requirements

- **No horizontal page scroll** below 768px on any page (board, detail sheet).
  Only explicitly-scrollable inner containers (history/artifact tables) may
  scroll sideways.
- **Tap targets** ≥44×44px for all interactive elements on the phone (nav
  items, card, restart button, tab-strip buttons).
- **No network dependency for styling** — `app.css` is a local static file;
  no external fonts/CDNs; the existing "no `https://` in body" guarantee is
  preserved.
- **No added latency**: this is CSS/markup/one-filter only; no new queries,
  no additional round trips (nav/tab switching is pure CSS/client JS, no new
  endpoints).
- **Accessibility baseline**: visible focus rings on interactive elements,
  `aria-current` on the active nav item, sufficient color contrast in both
  light and dark palettes for text/badges.
- **Backward compatibility of the read/write surface**: every existing route,
  its response shape, and every currently-pinned template assertion in
  `tests/test_api_html.py`, `tests/test_api_json.py`, `tests/test_api_sse.py`,
  `tests/test_api_artifacts.py` keeps passing unmodified.

## Data model

No changes. This task touches only `src/harness/api/templates/`,
`src/harness/api/static/`, and adds the `shorttime` filter registration in
`src/harness/api/routes.py`. `Task`, `Board`, `BoardColumn`, `HistoryEntry`,
`ArtifactRef` (all in `models.py` / `ports/`) are read exactly as today through
`BoardView` / `ArtifactView` / `StageOutputView` / `TaskControl` — no new
fields, no new ports.

## Interfaces

No route additions or signature changes. Existing endpoints keep their
contracts:
- `GET /` — full page shell (`board.html`), now includes `app.css` link, nav,
  and mobile meta tags.
- `GET /fragment/board` — `_columns.html`, restyled markup, same data
  contract (`board.revision`, `column.name`, `column.tasks|length`, per-task
  fields as today).
- `GET /fragment/task/{task_id}` — `_task.html`, restructured markup, same
  live-output SSE hookup (`GET /api/tasks/{task_id}/output/events`) and same
  `POST /tasks/{task_id}/restart` button.
- `GET /api/events`, `GET /api/board`, `GET /api/tasks/{id}`,
  `GET /api/tasks/{id}/artifacts[...]` — untouched (JSON/SSE, no template).
- `/static/app.css`, `/static/htmx.min.js`, `/static/sse.js` — static, served
  by the existing `StaticFiles` mount.

## Dependencies and scope

**In scope:**
- `src/harness/api/static/app.css` (new)
- `src/harness/api/templates/board.html`, `_columns.html`, `_task.html`
  (restyled/restructured)
- `src/harness/api/templates/_nav.html` (new partial, single "Board" entry)
- `src/harness/api/routes.py` — register the `shorttime` filter only
- Test updates limited to what the new markup requires (e.g. new
  `title`/`class` assertions), without weakening any existing assertion

**Out of scope (and why):**
- Any "Agents / Workflows" admin page restyling, and the bottom-tab-bar having
  more than a "Board" entry — that feature (ports, routes, templates) does not
  exist in this repository yet; building it would violate the task's own
  "presentation only, no port/route changes" constraint. `tests/test_api_
  agents.py` / `tests/test_api_workflows.py` do not exist and are not created
  by this task.
- Grouping the board by workflow template / a workflow tab-strip — `Board` has
  no such grouping today; nothing to add tabs "over".
- Any port, model, driver, or dispatcher/consumer/router change.
- Visual/pixel parity with the reference branch's CSS — it's guidance, not a
  merge source (too divergent from `main` to diff cleanly).

**Depends on:** the existing `BoardView`, `ArtifactView`, `StageOutputView`,
`TaskControl` ports and their current `htmx`/SSE wiring in `app.py` /
`routes.py` — all read-only from this task's perspective.

## Rough plan

1. **Design pass**: produce the concrete CSS token set (light + dark), the
   card/column/nav/sheet markup, and the exact tab-strip markup for the
   Info/History/Output control, informed by (not copied wholesale from) the
   reference branch's `app.css` and `_nav.html`/`_task.html`.
2. **Architecture pass**: confirm the template/static file boundaries stay
   inside `api/` (no drivers imported), decide where the `shorttime` filter
   and nav data live (routes.py vs. a small template-local list), and pin down
   which existing tests gate which new markup (a checklist against
   `tests/test_api_html.py`).
3. **Development**:
   a. Extract current inline CSS into `static/app.css`; wire the `<link>` into
      `board.html`; verify no regressions (`.venv/bin/pytest -q
      tests/test_api_html.py`).
   b. Add dark-mode custom properties; verify manually (or via a small CSS
      unit check) that both palettes are defined.
   c. Rework `_columns.html`/`.board`/`.column` CSS for stack-then-row
      responsive layout + sticky header + empty-state; keep existing text
      content matchers green.
   d. Rework `.card` CSS (stripe, pill badges, meta row) and add the
      `shorttime` filter usage + `title` attribute for the timestamp.
   e. Add `_nav.html` (bottom bar / top bar) and mobile meta tags in
      `board.html`'s `<head>`.
   f. Restructure `_task.html` into the Info/History/Output segmented sheet;
      keep the live-output block's hooks untouched; keep the restart button's
      `hx-*` attributes untouched.
   g. Add/adjust template-contract tests for the new markup (e.g. `title`
      attribute carrying the ISO timestamp, tab-strip button/panel ids) without
      touching the existing assertions.
4. **Review**: re-run the full suite (`.venv/bin/pytest -q`), specifically
   `tests/test_api_html.py`, `tests/test_api_json.py`, `tests/test_api_sse.py`,
   `tests/test_api_artifacts.py`, `tests/test_architecture.py`; manually check
   a 390px viewport and a ≥768px viewport in both color schemes (the `verify`
   skill can drive this if a real browser check is warranted).

## Open questions

1. **Admin agents/workflows UI and multi-workflow tabs.** The originating
   request assumes these exist; they don't. Default taken: drop them from this
   task's scope entirely (see Dependencies and scope). If an admin UI is
   wanted, it should be its own task that first adds the port(s)/route(s), with
   this redesign's shared stylesheet/nav then extended to cover it.
2. **Sheet implementation for the phone (`<dialog>` vs. fixed `<div>`).** Not
   yet decided — a CSS-only full-screen `<dialog>` (`dialog#detail { inset: 0;
   width: 100%; height: 100%; max-width: none; }` inside a phone media query)
   keeps the existing `showModal()`/`close()` JS and backdrop-click handling
   completely unchanged, which is why it's the likely default; the design pass
   should confirm this works with `sse-close="end"` teardown on close.
3. **How many columns show side-scroll at ≥768px with many workflows.** Today's
   `.board` already allows `overflow-x: auto` as a fallback; the redesign
   keeps that fallback rather than trying to solve "too many columns," which
   is unchanged from the current behaviour.
4. **Where the `shorttime` cutover applies.** Default: card `.since` and any
   detail-sheet timestamp use `shorttime` for display + ISO in `title`; the
   History table's `time` column keeps the ISO string as visible text (it's a
   dense audit table where sortable/greppable full timestamps are more useful
   than on a card) — confirm in the design pass rather than assuming both use
   the short form.
