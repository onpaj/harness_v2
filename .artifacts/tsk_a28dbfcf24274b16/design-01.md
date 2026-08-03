# Design: board UI doesn't use full width on wide desktop screens

Builds on `plan-01.md`. Confirms the plan's scope (CSS-only, `app.css` only) and
picks concrete breakpoints, values and rule placement.

## UX/UI

This is a layout-only change to an existing UI (no new screens, no new
interactive behavior) — but it changes how the board reads at wide sizes, so
wireframes below show the column-growth effect the new breakpoints produce.

### Breakpoint ladder (new tiers in bold)

| Viewport | `.page` max-width | `.column` max-width | Layout |
|---|---|---|---|
| ≤767px (phone) | n/a (`.page` cap still applies but columns stack) | n/a | vertical stack, tabbar, unchanged |
| 768–1199px | 1200px (unchanged) | 360px (unchanged) | row layout, unchanged |
| 1200–1399px | 1200px (unchanged) | 360px (unchanged) | row layout, unchanged |
| **1400–1799px** | **1600px** | **420px** | row layout, columns/page wider |
| **≥1800px** | **2200px** | **500px** | row layout, columns/page widest |

Two new tiers, both strictly additive `@media (min-width: …)` blocks placed
*after* the existing `@media (min-width: 768px)` block — the 768px block's
rules (row flex-direction, tabbar hiding, dialog sizing, `.kv .k` basis) are
untouched, so every rule the current tests assert on (`flex-direction: row`
inside `@media (min-width: 768px)`) keeps matching byte-for-byte.

`.page`'s max-width only ever grows going right along this table — at no
tier does a wider viewport get a *smaller* cap than a narrower one, so there's
no snapping/shrinking discontinuity when resizing across 1400/1800.

### ASCII sketch — board strip at increasing width

At 768–1400px (today, unchanged):
```
┌── viewport ─────────────────────────────────────────┐
│  ┆        .page (max 1200px, centered)        ┆     │
│  ┆  ┌────────┐ ┌────────┐ ┌────────┐ ┌──────┐  ┆     │
│  ┆  │ Waiting│ │ step-A │ │ step-B │ │Finish│  ┆     │
│  ┆  │ (280 ) │ │ (280 ) │ │ (280 ) │ │(280) │  ┆     │
│  ┆  │ long   │ │        │ │        │ │      │  ┆     │
│  ┆  │ title  │ │        │ │        │ │      │  ┆     │
│  ┆  │ wraps  │ │        │ │        │ │      │  ┆     │
│  ┆  │ 4 lines│ │        │ │        │ │      │  ┆     │
│  ┆  └────────┘ └────────┘ └────────┘ └──────┘  ┆     │
└───────────────────────────────────────────────────────┘
```

At ≥1800px (new — columns grow, page cap relaxed, title wraps less):
```
┌── viewport (2000-2560px) ───────────────────────────────────────────────┐
│ ┆            .page (max 2200px, centered, tiny gutter only)      ┆     │
│ ┆ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐        ┆     │
│ ┆ │  Waiting  │ │  step-A   │ │  step-B   │ │  Finished │        ┆     │
│ ┆ │  (~460)   │ │  (~460)   │ │  (~460)   │ │  (~460)   │        ┆     │
│ ┆ │  long     │ │           │ │           │ │           │        ┆     │
│ ┆ │  title    │ │           │ │           │ │           │        ┆     │
│ ┆ │  wraps 2  │ │           │ │           │ │           │        ┆     │
│ ┆ └───────────┘ └───────────┘ └───────────┘ └───────────┘        ┆     │
└──────────────────────────────────────────────────────────────────────────┘
```
(Column width above is `min(500px, available/N)` — actual width still depends
on how many columns the served workflow has; the cap only prevents columns
from becoming absurdly wide when a board has just 2-3 columns.)

### Other pages / dialogs — confirmed as no-op, not just assumed

Checked each `.page`-using surface against the new, larger `.page` cap:

- **Agents / Workflows / Processes list pages**: content sits in
  `.process-list`/`.empty-state` (`max-width: 720px`) or plain text — these
  inner caps are independent of `.page` and already stop the content from
  stretching edge-to-edge. Wider `.page` just means more side margin at
  ≥1800px, which is correct (a list of short rows should not stretch to
  2200px of line length).
- **Process editor** (`.page--editor { max-width: 760px }`), **agent/workflow
  editors** (`.panel { max-width: 640px }`, `.panel.wide { max-width: 720px }`),
  **danger zone** (`max-width: 760px`): same reasoning — inner caps already
  narrower than even the *current* 1200px `.page` cap, so they were never
  driven by it and stay visually identical at every width.
- **`dialog#detail` / `dialog#add-issue`**: sized by their own desktop rule
  `width: min(860px, 94vw)` (`app.css:385`), which reads viewport width
  directly, not `.page`. Confirmed unaffected by any `.page`/`.column` edit —
  no change needed or made. At 2560px this still centers an ~860px dialog,
  which is the right call (a modal shouldn't fill a 2560px screen).
- **Workflow preview SVG / tables**: already wrapped in their own
  `overflow-x: auto` containers (`.workflow-preview__scroll`, `.table-scroll`);
  a wider `.page` gives them more room before they'd need to scroll, never less.

No markup/template changes anywhere — `board.html`/`_columns.html` structure
(`.page` → `.board` → `.column` → `.card`) is untouched.

## Component design

Single component in scope: the shared stylesheet `src/harness/api/static/app.css`.
No new CSS classes, no JS, no template changes. The "component boundary" here
is purely the set of existing selectors already responsible for width:

| Selector | Current rule (line) | Change |
|---|---|---|
| `.page` | `max-width: 1200px` (145) | add two overriding rules inside new media blocks |
| `.column` (desktop) | `flex: 1 1 0; min-width: 260px; max-width: 360px` (377, inside `@media (min-width: 768px)`) | add two overriding `max-width` values inside new media blocks; `flex`/`min-width` untouched at every tier |
| *(new)* `@media (min-width: 1400px)` | — | new block, placed immediately after the existing `@media (min-width: 768px)` block (after line 394) |
| *(new)* `@media (min-width: 1800px)` | — | new block, placed immediately after the 1400px block |

Concrete rules to add (implementation step applies these verbatim):

```css
/* --- Wide desktop --------------------------------------------------------- */

@media (min-width: 1400px) {
  .page { max-width: 1600px; }
  .column { max-width: 420px; }
}

@media (min-width: 1800px) {
  .page { max-width: 2200px; }
  .column { max-width: 500px; }
}
```

Placement rationale: appending after the existing `@media (min-width: 768px)`
block (rather than folding the new values into it) keeps that block's content
identical for the existing test assertion (`"@media (min-width: 768px)" in css`
plus `"flex-direction: row"` inside it, `tests/test_api_html_mobile.py:90-91`),
and keeps the diff a pure addition — reviewable independently of the base
desktop rules. Because each new block only overrides `max-width` (never
`flex`, `min-width`, `flex-direction`, or any color/spacing token), cascade
order is safe regardless of source order among the three media blocks — later
blocks in the file win only because CSS applies the last matching rule among
equal-specificity selectors, and here every one of the three is more specific
by media condition, not selector, so there's no specificity fight, only
"which media query matches" — at 2000px only the 768px and 1400px and 1800px
queries all match simultaneously, and since 1800px's block is declared last,
its `max-width` values win over 1400px's and 768px's for `.page`/`.column`.
This ordering (768 → 1400 → 1800, ascending, each appended after the last)
must be preserved — reordering would silently invert which tier wins.

No component boundary changes: `.board`, `.card`, `.column__head`,
`.tab-strip`, dialogs, and every admin-page selector are untouched.

## Data schemas

Not applicable — presentation-only CSS change. No request/response shape,
DB schema, or event payload is touched.

## Verification plan for the next (development) step

1. Add the two `@media` blocks exactly as specified above, after the existing
   768px block.
2. Run `.venv/bin/pytest -q`, specifically `tests/test_api_html_mobile.py` and
   `tests/test_api_html.py`, to confirm the existing CSS-content assertions
   still pass.
3. Visual check (via the `run`/browser tooling) of the board at 375, 768,
   1200, 1400, 1800, 1920 and 2560px, plus Agents/Workflows/Processes and both
   dialogs at 1920/2560px, against the acceptance criteria in the task:
   - 375/768/1200px: pixel-identical to current build.
   - 1400–1800px: `.page` visibly wider than 1200px, columns visibly wider
     than 360px.
   - 1920/2560px: board strip reaches close to both edges, card titles wrap
     less; dialogs still ~860px wide; admin pages' inner panels unchanged in
     width; no `body` horizontal scrollbar at any tested width.
