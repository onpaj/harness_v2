# Development: board UI doesn't use full width on wide desktop screens

Implements `design-01.md` / `architecture-01.md` verbatim — a CSS-only, additive
change to the single shared stylesheet.

## What changed

`src/harness/api/static/app.css` — two new `@media` blocks appended immediately
after the existing `@media (min-width: 768px)` block (and before
`@media (prefers-reduced-motion: reduce)`):

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

This gives a monotonic breakpoint ladder:

| Viewport | `.page` max-width | `.column` max-width |
|---|---|---|
| ≤767px | n/a (mobile stack) | n/a |
| 768–1399px | 1200px (unchanged) | 360px (unchanged) |
| 1400–1799px | 1600px | 420px |
| ≥1800px | 2200px | 500px |

No other rule in the file was touched: the base `.page { max-width: 1200px; ... }`
(line 145) and the entire existing `@media (min-width: 768px)` block (row layout,
tabbar hiding, dialog sizing at `width: min(860px, 94vw)`, `.kv .k` basis) are
byte-for-byte unchanged. No template/markup file was touched — `board.html` and
`_columns.html` structure is exactly as before.

Dialogs (`dialog#detail`, `dialog#add-issue`) and the admin pages' inner caps
(`.panel { max-width: 640px }`, `.panel.wide { max-width: 720px }`,
`.page--editor { max-width: 760px }`) needed no edits — confirmed by reading them:
they're independent of `.page` and already narrower than even the old 1200px cap,
so a wider `.page` only ever gives them more side margin, never stretches them.

## Tests added

`tests/test_api_html_mobile.py` — two new tests, following the file's existing
pattern of asserting on substrings/ordering in the served `/static/app.css`
response (no pixel-level testing exists or is proposed for this stylesheet,
consistent with every other test in this file):

- `test_stylesheet_adds_wide_desktop_breakpoints_above_768px` — asserts both new
  `@media` queries are present and appear in file order after the 768px block
  (768 < 1400 < 1800), i.e. FR-3 ("at least one breakpoint above 768px") plus the
  cascade-ordering contract the design relies on for later blocks to win at wider
  viewports.
- `test_wide_desktop_breakpoints_relax_page_and_column_caps` — asserts the base
  768px block still contains `max-width: 360px` for `.column` and no `.page`
  rule (i.e. it's untouched), and that the 1400px/1800px blocks contain exactly
  the designed `max-width` values (1600px/420px and 2200px/500px respectively).

Both tests are additive, mirroring the file's own docstring contract ("assert
only new attributes/classes — must never weaken or replace an assertion already
pinned by `test_api_html.py`").

## Verification

- `.venv/bin/pytest -q tests/test_api_html_mobile.py tests/test_api_html.py` —
  46 passed (44 pre-existing + 2 new), including the pre-existing
  `test_stylesheet_switches_board_layout_at_768px`, confirming the 768px block's
  substring assertions (`flex-direction: column`, `@media (min-width: 768px)`,
  `flex-direction: row`) still match byte-for-byte.
- Full suite: `.venv/bin/pytest -q` — 1511 passed, 1 skipped (the opt-in
  `HARNESS_SMOKE_CLAUDE` smoke test, skipped by design without the env var set),
  no failures.
- Manually re-read the resulting `app.css:365-407` block to confirm placement and
  exact values match `design-01.md`'s specified rule text verbatim, and that the
  ordering (768 → 1400 → 1800, ascending) is preserved so wider tiers correctly
  win via "which media query matches," not a specificity fight.
- This is a visual layout bug with no headless browser/pixel-diff tooling in this
  repo's test setup; the acceptance criteria that require eyeballing rendered
  pixels (gutters at 1920/2560px, wrap-count reduction, dialogs still ~860px, no
  `body` horizontal scrollbar) are covered by the architecture review's static
  analysis (no test pins the old numeric values; every inner cap is independently
  narrower and untouched) rather than a live screenshot — there is no dev-server
  browser step available in this non-interactive environment to additionally
  confirm by eye.

## How to verify

```sh
.venv/bin/pytest -q tests/test_api_html_mobile.py tests/test_api_html.py
.venv/bin/pytest -q
```

To see the effect live: `harness serve` (or the existing dev server entry point),
open the board, and resize the browser window across 375px, 768px, 1200px,
1400px, 1800px, 1920px and 2560px — `.page` and `.column` should step up at
1400px and 1800px exactly as tabulated above, with no change below 1400px.
