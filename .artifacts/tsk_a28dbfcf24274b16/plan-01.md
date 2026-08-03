# Plan: board UI doesn't use full width on wide desktop screens

## Summary

`src/harness/api/static/app.css` hard-caps every page's `.page` shell at
`max-width: 1200px` and gives board columns a fixed `width`/`min-width`/`max-width`
(`.column { min-width: 260px; max-width: 360px }` inside the existing
`@media (min-width: 768px)` block). On viewports beyond ~1200px this leaves large
empty gutters while card titles inside the columns wrap harder than they need to.
This is a CSS-only fix: add one or two wider breakpoints that relax the page cap
and let columns grow, without touching the 375px/768px/1200px behavior or any
template/markup.

## Context

Reported directly against the board UI at ~2000px viewport width. `app.css` is the
single stylesheet shared by every page (board, task-detail dialog, add-issue
dialog, and the four admin pages: Agents, Workflows, Processes — there is
currently no separate "Stats" page in `_nav.html`, see Open Questions). All of
these pages wrap their content in `<main class="page">`, so the fix is
concentrated in a handful of rules in one file.

## Functional requirements

**FR-1 — Raise/relax the `.page` cap above 1200px.**
Replace the flat `max-width: 1200px` with a rule that grows past it on wide
viewports (e.g. `max-width: min(1800px, 100%)` or an explicit wide breakpoint
that raises `max-width`), while staying byte-identical in rendered layout at
≤1200px.
- AC: computed `max-width` of `.page` at 1920px and 2560px viewport width is
  visibly greater than 1200px (e.g. ~1800px), verified by opening the board at
  those sizes and confirming the strip reaches close to both edges.
- AC: at 375px, 768px and 1200px viewport width, computed `.page` max-width/padding
  is unchanged from the current build (no visual diff).

**FR-2 — Let board columns grow with spare width.**
Change `.column`'s desktop sizing (currently `flex: 1 1 0; min-width: 260px;
max-width: 360px` under `@media (min-width: 768px)`) so columns can use more
space once the page shell itself has grown — e.g. raise `max-width` at the new
wide breakpoint(s), or drop the cap there while keeping `flex: 1 1 0` and
`min-width` as the floor.
- AC: at 1920px+ with the `default`/`development` workflow's typical column
  count, columns are visibly wider than 360px and a task title that wraps to
  3+ lines today wraps to fewer lines.
- AC: at 768–1200px, `.column` sizing is unchanged (still capped at 360px as
  today).

**FR-3 — Add at least one breakpoint above 768px.**
Introduce a new `@media (min-width: 1400px)` (and optionally a second at
`~1800px`) block that layers on top of the existing 768px block — additive,
not a replacement of it — carrying the relaxed `.page` and `.column` rules
from FR-1/FR-2.
- AC: `app.css` contains at least one `@media (min-width: …)` query with a
  threshold strictly greater than 768px.
- AC: the existing `@media (min-width: 768px)` block and its rules are
  untouched in content (board becomes row layout, tabbar hides, etc.).

**FR-4 — No regressions elsewhere.**
Every other page/dialog that uses `.page` (Agents, Workflows, Processes,
`.page--editor` for the process form) and the two dialogs (`dialog#detail`,
`dialog#add-issue`, currently `width: min(860px, 94vw)`) are checked at wide
widths and left alone unless they visibly misbehave.
- AC: dialogs still cap near ~860px at 1920px+ (confirmed, not changed) —
  a modal that fills the whole 2560px screen would be worse, not better.
- AC: Agents/Workflows/Processes list and editor pages read reasonably at
  1920px/2560px (text/panels not stretched edge-to-edge into unreadable long
  lines) — `.panel { max-width: 640px }` / `.panel.wide { max-width: 720px }`
  and `.page--editor { max-width: 760px }` stay as inner caps regardless of
  the outer `.page` cap change, so these should need no edits, only visual
  confirmation.
- AC: no horizontal scrollbar appears on `body` at any tested width (375,
  768, 1200, 1400, 1920, 2560); `.board`'s own `overflow-x: auto` remains the
  only horizontal-scroll surface.

## Non-functional requirements

- Pure CSS change — no new JS, no template/markup changes expected (FR-1–FR-3
  are achievable entirely inside `app.css`).
- Preserve existing test contracts: `tests/test_api_html_mobile.py` asserts
  `"@media (min-width: 768px)"` is present in the served CSS and that it
  contains `flex-direction: row` — the new breakpoint(s) must be added
  alongside this block, not by editing its content.
- No change to color scheme, spacing tokens, or any non-width CSS custom
  property.

## Data model

Not applicable — presentation-only change, no data/model impact.

## Interfaces

- `src/harness/api/static/app.css` — `.page` (currently line 145), `.column`
  desktop sizing inside `@media (min-width: 768px)` (currently lines
  367–394), and a new `@media (min-width: 1400px)` (and optionally `1800px`)
  block appended after it.
- No route, template, or JS changes anticipated. `board.html` / `_columns.html`
  markup structure (`.page` → `.board` → `.column` → `.card`) stays as-is;
  this is a styling-only pass over existing selectors.

## Dependencies and scope

- In scope: `app.css` width/breakpoint rules for `.page` and `.column`
  (and, only if visual review shows they need it, the dialog widths — expected
  to be a no-op per FR-4).
- Out of scope: any change to card content, badges, column grouping logic,
  workflow tab strip, or the admin form layouts' own internal max-widths
  (`.panel`, `.page--editor`, `.process-list`, `.danger-zone`, etc.) — these
  already have independent, tighter caps and are not implicated by the bug
  report.
- Out of scope: a "Stats" page — it does not currently exist in `_nav.html`
  (only Board, Agents, Workflows, Processes); see Open Questions.
- Depends on nothing beyond the existing static CSS pipeline (no build step —
  `app.css` is served directly).

## Rough plan

1. **Design pass** (next step): pick concrete breakpoint(s) and values —
   e.g. `@media (min-width: 1400px) { .page { max-width: 1800px } .column {
   max-width: 460px } }`, plus whether a second `1800px`/`2000px` tier is
   warranted for 2560px+ screens. Confirm the dialog and admin-page caps
   genuinely need no change (FR-4) rather than assuming it.
2. **Implement**: edit `src/harness/api/static/app.css` only — add the new
   breakpoint block(s) after the existing `@media (min-width: 768px)` block,
   relaxing `.page` and `.column` sizing. Leave every other rule untouched.
3. **Verify visually**: use the `run`/browser tooling to load the board (and
   Agents/Workflows/Processes pages, plus the task-detail and add-issue
   dialogs) at 375px, 768px, 1200px, 1400px, 1920px and 2560px; confirm the
   acceptance criteria above by eye (no automated pixel test exists or is
   proposed — this is a visual/layout bug).
4. **Run the test suite** (`.venv/bin/pytest -q`, in particular
   `tests/test_api_html_mobile.py` and `tests/test_api_html.py`) to confirm no
   existing CSS-content assertion broke.

## Open questions

- The bug report mentions a "Stats" page among the pages to check, but
  `_nav.html` currently lists only Board / Agents / Workflows / Processes —
  assuming this is either a future/renamed page or a minor inaccuracy in the
  report; no Stats-specific work is planned. If a Stats page exists under a
  different route, the design step should locate it and confirm it also uses
  `.page`.
- Exact breakpoint thresholds and `max-width` values are left to the design
  step to pick concretely (e.g. 1400px/1800px vs. 1440px/1920px) — the request
  only requires "at least one breakpoint above 768px" and gives illustrative
  numbers, not fixed ones.
