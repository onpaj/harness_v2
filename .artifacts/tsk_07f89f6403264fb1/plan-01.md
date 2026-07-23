# Plan: Make the harness board mobile friendly

## Summary

The harness board (`src/harness/api/templates/{board,_columns,_task}.html`) is a
server-rendered Jinja + htmx + SSE kanban UI with all styling inlined in a single
`<style>` block in `board.html` — no CSS framework, no build step, no JS bundler.
It currently has no `<meta name="viewport">` tag, fixed-width columns, a
fixed-max-width modal dialog, and wide `<table>`-based detail views, none of which
adapt to a small screen. This task makes the existing UI render usably on mobile
viewports (~320–430px wide) without introducing a framework, build step, or new
dependency — pure CSS (media queries) plus minimal, additive template markup.

## Context

The board is the only UI this project ships (`docs/superpowers` phase specs don't
mention a mobile requirement, so this is a standalone UI-quality task). It's used
to watch tasks move through the harness's queues and to restart failed ones. Since
the whole point of the board is "check in on what's running," being usable from a
phone (e.g., checking on a task away from a desk) has real value, and today the
page renders at desktop width (~980px, the default mobile browser viewport
fallback) and gets scaled down illegibly because there's no viewport meta tag at
all — the most basic mobile-rendering prerequisite is missing.

## Functional requirements

**FR-1 — Correct viewport scaling.**
`board.html` declares `<meta name="viewport" content="width=device-width,
initial-scale=1">`. Acceptance: page renders at device width on load, no
pinch-out-then-readable-text step needed; `test_api_html.py`'s index test can
assert the meta tag is present in the response body.

**FR-2 — Kanban columns are usable on a narrow screen.**
The `.board` / `.column` layout in `_columns.html` remains a horizontally
scrollable row of columns (the Trello-style pattern already in place via
`overflow-x: auto`), but on narrow viewports each column's width is a function of
the viewport (e.g. ~85–90vw) rather than a fixed `min-width: 220px` competing with
`flex: 1`, so exactly one column (plus a sliver of the next) is visible at a time
and it's obvious the row scrolls. Acceptance: at 375px width, a single column is
fully visible without needing to zoom, and swiping left/right moves between
columns.

**FR-3 — Cards and badges are legible and tappable.**
Card title, repo line, and badges keep their current information density but the
card's tappable area and any interactive text meet a ~44px minimum touch-target
height where they trigger `hx-get`. Acceptance: on a 375px-wide screen, a card can
be tapped without needing to zoom in, and no text is clipped or overlapping.

**FR-4 — Task detail dialog is usable on mobile.**
The `<dialog id="detail">` in `board.html` currently caps at `max-width: 640px;
width: 90%`, which is fine on mobile width-wise, but its content (wide tables) will
overflow. On narrow viewports the dialog grows to use most of the viewport height
(scrollable internally) and its content reflows per FR-5. Acceptance: opening a
task's detail on a 375×667 viewport shows the dialog without horizontal page
scroll and with a visible, tappable way to close it (existing backdrop-click-to-
close stays; no new close button is required unless review flags backdrop-only
close as insufficient on touch — see Open Questions).

**FR-5 — Detail tables don't force horizontal page scroll.**
The `key/value` table and the `history` / `artifacts` tables in `_task.html` are
wrapped so that on narrow viewports they scroll horizontally *within their own
container* (`overflow-x: auto` on a wrapper) rather than forcing the whole dialog
or page to scroll sideways. Acceptance: at 375px width, the dialog's outer edges
stay fixed; only a table itself scrolls horizontally if its content is wider than
the viewport.

**FR-6 — Live-output pane remains usable.**
The SSE live-output `<div id="stage-output">` (monospace log) keeps its
`max-height` and internal scroll, but its font-size and padding are checked against
mobile legibility (no shrinking below ~12px) and it doesn't force the dialog wider
than the viewport (`white-space: pre-wrap` already wraps long lines — confirm this
holds at narrow widths rather than relying on horizontal scroll).

**FR-7 — Restart action stays reachable.**
The `Restart` button in `_task.html` (shown for `failed` tasks) meets the same
~44px touch-target height as FR-3 and remains visible without extra scrolling
inside the dialog header area.

## Non-functional requirements

- **No new dependencies.** No CSS framework (Tailwind, Bootstrap), no JS framework,
  no build step / bundler. This project serves two static files verbatim
  (`htmx.min.js`, `sse.js`) and inlines all CSS in `board.html`; that stays true —
  changes are additive CSS (media queries) and minor, non-semantic markup
  (e.g. a wrapper `<div>` around tables for scroll containment).
- **No backend/API changes.** `routes.py`, ports, ArtifactView, BoardView etc. are
  untouched — this is a template/CSS-only change. Guarded implicitly by
  `test_architecture.py` (nothing there to violate, but a reviewer should confirm
  no drift into `api/routes.py`).
- **Existing tests keep passing unmodified in behavior.** `test_api_html.py`,
  `test_api_sse.py`, `test_board_e2e.py`, `test_api_json.py`,
  `test_api_artifacts.py` assert on text content and hx- attributes / ids, not
  exact CSS — see verification below. Any test additions are additive
  (e.g. asserting the viewport meta tag exists), not replacements.
- **No regression on desktop.** Changes are expressed as mobile-first CSS with a
  breakpoint (e.g. `min-width: 768px` for the current desktop layout), or as
  desktop-first with a `max-width` breakpoint override — either way the existing
  wide-viewport appearance is preserved.
- **Touch targets** meet a practical ~44×44px minimum on interactive elements
  (card, restart button) per common mobile accessibility guidance (this is a
  usability bar, not a formal a11y audit — a full accessibility pass is out of
  scope).

## Data model

None. No entities, ports, or persisted state change — this task only touches
presentation (`templates/*.html`, the inline `<style>` in `board.html`).

## Interfaces

No endpoint, event, or route changes. The three templates and their responsive
behavior:

- `board.html` — adds the viewport meta tag; adds/extends media queries for
  `.board`, `.column`, and `dialog` sizing; no change to the htmx/SSE wiring
  (`hx-get`, `sse-connect`, the revision de-dup script) or element ids the
  inline `<script>` depends on (`#board`, `#detail`).
- `_columns.html` — column width becomes viewport-relative under a breakpoint;
  card markup unchanged (same `hx-get`/`hx-target` attributes), only class/style
  additions for tap-target sizing.
- `_task.html` — tables gain a scroll-wrapper `<div>` (or equivalent CSS) for
  narrow viewports; `Restart` button and the live-output `<div>` get responsive
  sizing tweaks. `hx-post`, `hx-target`, `sse-connect`, and element ids
  (`#stage-output`) are unchanged.

## Dependencies and scope

**Rests on:** the current board implementation as-is (Jinja templates, inline
CSS, htmx + SSE, no build pipeline) — this task works within that architecture,
it doesn't change it.

**In scope:**
- `src/harness/api/templates/board.html`, `_columns.html`, `_task.html`
- Possibly a small addition to `test_api_html.py` asserting the viewport meta tag

**Out of scope:**
- Introducing a CSS/JS framework or a build step
- Redesigning the visual style (colors, information architecture) beyond what's
  needed for responsiveness
- A full accessibility (WCAG) audit — only the touch-target sizing called out
  above
- Changing the dialog to a routable full page, or any other routing/behavior
  change (see Open Questions)
- Automated visual/screenshot regression testing — this repo has none today and
  none is being introduced; verification is manual (browser devtools device
  emulation) per FR acceptance criteria, plus the existing text-content-based
  HTML tests

## Rough plan

1. **Add the viewport meta tag** to `board.html` (FR-1).
2. **Make the kanban row responsive** in the shared `<style>` block in
   `board.html`: replace the fixed `min-width: 220px` on `.column` with a media
   query so narrow viewports get a viewport-relative column width (e.g. `85vw`),
   keeping the existing `overflow-x: auto` horizontal-scroll pattern (FR-2).
3. **Size cards and the restart button for touch**: adjust `.card` padding and
   the button's padding/min-height for a ~44px target on narrow viewports (FR-3,
   FR-7).
4. **Make the dialog responsive**: add a media query so `dialog` grows towards
   full-viewport width/height on narrow screens with internal scroll, instead of
   the fixed `max-width: 640px; width: 90%` alone (FR-4).
5. **Wrap tables for horizontal containment** in `_task.html`: add a
   `<div style="overflow-x:auto">` (or a CSS class defined in `board.html`'s
   style block) around each `<table>` so a too-wide table scrolls in place (FR-5).
6. **Check the live-output pane** at narrow widths — confirm `pre-wrap` avoids
   horizontal overflow, adjust font-size/padding only if needed (FR-6).
7. **Add a viewport-meta assertion** to `test_api_html.py`'s existing index test
   (the only new-test surface — the rest is CSS/layout that this project doesn't
   have infrastructure to assert on).
8. **Manually verify** in browser devtools device emulation at a small width
   (e.g. 375×667) and a larger phone width (e.g. 430px): board scroll, card tap,
   dialog open, table scroll, live-output SSE stream, restart flow — then spot-
   check the same interactions at a desktop width to confirm no regression.
9. **Run `.venv/bin/pytest -q`** to confirm the full suite (including the smoke
   tests) still passes.

## Open questions

- **Backdrop-only dialog close on touch** — the current close mechanism is a
  click on the `<dialog>` backdrop (`event.target.id === 'detail'`) plus the
  browser's native `<dialog>` behavior. Touch backdrop-dismiss works the same as
  click in mobile browsers, so the default here is **no explicit close button
  added** unless manual testing shows it's not discoverable; noting it as a risk
  rather than blocking on it.
- **Vertical-stack vs. horizontal-scroll columns** — chose to keep the existing
  horizontal-scroll (Trello-style) pattern rather than stacking columns
  vertically, since it preserves the at-a-glance workflow view and requires the
  smallest change. Default: horizontal scroll, narrower columns. If review
  prefers vertical stacking on mobile, that's a bigger template change
  (collapsing `.board`'s `flex-direction` under a breakpoint) — flagged here so
  it's a deliberate choice, not an oversight.
- **Table responsiveness strategy** — chose the simpler "scrollable wrapper"
  approach over a CSS-only "stacked label/value rows" transform (e.g. `display:
  block` on `tr`/`td` with `data-label` attributes), since the latter needs
  extra markup per cell and more CSS complexity for marginal gain on tables that
  are already narrow (2-column key/value) or short (history/artifacts). Default:
  scroll wrapper for all three tables.
- **No target device/browser list was given** — assuming modern mobile Safari
  and Chrome (the two realistic targets for checking a personal dev tool from a
  phone); no IE/legacy support implied or required.
