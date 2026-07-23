# Development — Mobile-first board and admin UI redesign

Implements `plan-01.md` / `design-01.md` / `architecture-01.md` unchanged in
scope: the **board** and **task-detail** surfaces only (the admin
agents/workflows UI does not exist in this repo and is correctly out of
scope, per the plan's scope correction — building it would require inventing
ports/routes this task's brief explicitly forbids).

## Files changed

- `src/harness/api/routes.py` — added `_shorttime`, registered as the
  `shorttime` Jinja filter next to the existing `basename` filter. This is
  the only `.py` diff in the task, as required (FR-5 / architecture §1).
- `src/harness/api/static/app.css` (new) — the single shared stylesheet.
  CSS custom properties for all design tokens on `:root`, a full
  `prefers-color-scheme: dark` override block, one `@media (min-width: 768px)`
  block that flips the board from stacked (`flex-direction: column`) to
  side-by-side (`row`) and switches the tab bar/app bar roles — no JS
  involved in that switch. No `@import`, no external URL of any kind
  (verified: `grep "://"` on the file returns nothing).
- `src/harness/api/templates/_nav.html` (new partial) — a single
  Jinja-local `nav_items` list (today: one "Board" entry), rendered once as
  a desktop `appbar` and once as a phone `tabbar` from the same loop, plus a
  small inline script that marks the matching link `.active` /
  `aria-current="page"` by comparing `location.pathname`. Adding a future
  Agents/Workflows entry is a one-line list addition, not a rewrite.
- `src/harness/api/templates/board.html` — added the mobile viewport/
  theme-color/apple-mobile-web-app-* meta tags, the `app.css` link, the
  `_nav.html` include, and one new delegated click listener for
  Info/History/Output tab switching registered on `#detail` (survives the
  `_task.html` fragment being wholesale-replaced on every swap — the
  architecture doc's Decision 3). The three pre-existing listeners
  (dialog-open-on-swap, backdrop-click-to-close, innerHTML-clear-on-close
  which tears down the Output SSE connection) and the revision de-dup
  script are unchanged in logic, only regrouped.
- `src/harness/api/templates/_columns.html` — restyled into sticky column
  headers with a count pill, an empty-column placeholder ("No tasks"), and
  cards with a colored left status stripe (`is-working`/`is-done`/
  `is-changes`/`is-failed` — derived the same way the existing
  `.badge.working`/`.badge.{{ last_outcome }}` conditionals already were, no
  new field), pill badges, and a `card__time` element carrying the
  `shorttime`-formatted text with the raw ISO string in `title=`. No
  workflow-tab-strip wrapper was added — `Board` has no such grouping in
  this repo (confirmed against `ports/board.py`), so there is nothing to add
  tabs "over"; this matches the architecture doc explicitly.
- `src/harness/api/templates/_task.html` — restructured into the segmented
  sheet: header (title, conditional Restart button, close button), a
  3-button Info/History/Output tab strip, and three panels. Info is a
  stacked key/value list (`.kv`) instead of a table; History and Artifacts
  render inside their own `.table-scroll` container so only they scroll
  horizontally, never the page. The Output panel's SSE wiring
  (`hx-ext="sse"`, `sse-connect`, `sse-close="end"`, `id="stage-output"`,
  `sse-swap="line"`, `hx-swap="beforeend scroll:bottom"`) is carried over
  byte-for-byte; only its inline `style=` was moved into the new `.terminal`
  CSS class. Dropped the reference branch's `task.step` row — this repo's
  `Task` model (`models.py`) has no `step` field.
- `tests/test_shorttime_filter.py` (new) — unit tests for `_shorttime`:
  `Z`-suffixed and explicit-offset timestamps, `None`, empty string, and
  unparseable input (defensive pass-through, matching `_basename`'s
  contract).
- `tests/test_api_html_mobile.py` (new) — contract tests for the new markup
  only (never replacing/loosening an existing assertion): stylesheet link
  and no external URL in it, viewport/apple meta tags present, safe-area CSS
  present, the 768px layout-switch media query, the nav renders the "Board"
  entry twice (appbar + tabbar), empty-column placeholder, card status
  class, the Info/History/Output tab-strip's exact `data-tab`/`data-panel`
  attributes, the Output panel's SSE hooks still verbatim, History wrapped
  in `.table-scroll`, and `shorttime` text paired with the full ISO string
  in a `title` attribute.

## Verification

- `.venv/bin/pytest -q` → **489 passed, 1 skipped** (the skip is the
  pre-existing opt-in `HARNESS_SMOKE_CLAUDE` test, unrelated to this task).
  This is the pre-existing 472 plus 17 new tests, with zero existing
  assertions modified.
- `tests/test_api_html.py` (all pinned card/column/detail/restart
  assertions), `tests/test_api_json.py`, `tests/test_api_sse.py`,
  `tests/test_api_artifacts.py` (including
  `test_api_imports_only_the_artifact_port_not_drivers`) and
  `tests/test_architecture.py` (including
  `test_api_does_not_import_drivers`) all pass unmodified — confirmed by
  reading their current content before writing any template change and by
  the green full-suite run above.
- Manually confirmed: `grep "://" src/harness/api/static/app.css` → no
  matches (no external resource); CSS brace count is balanced (96/96); no
  test in the suite matches on the removed inline `style=` attribute the
  Output `<div>` used to carry.
- No visual/browser-based check was run in this environment (no headless
  browser available); the CSS/markup was written and reasoned about
  structurally (media query boundaries, `flex-direction` switch, safe-area
  `env()` insets, `<dialog>` full-screen-on-phone / centered-on-desktop
  sizing) rather than screenshotted. A follow-up manual pass at 390px and
  ≥768px in both color schemes (e.g. via the `verify` skill with a real
  browser) is recommended before merging if a visual sign-off is required.

## Acceptance criteria status

- [x] No horizontal page scroll on a phone-width viewport — `.board` is
      `flex-direction: column` below 768px; cards are block-level full-width.
- [x] `≥768px` keeps the side-by-side kanban (`flex-direction: row` in the
      `min-width: 768px` media query, unchanged column/card data contract).
- [x] Light and dark both defined via `:root` custom properties + the
      `prefers-color-scheme: dark` override block.
- [x] Bottom tab bar (phone) / inline nav (desktop) present, single "Board"
      entry (per scope), active-state via `data-section` + `aria-current`.
- [x] Task detail full-screen sheet on phone / centered dialog on desktop,
      Info/History/Output tabs working via the delegated `#detail` listener;
      Output SSE hooks unchanged.
- [x] Wide tables (`History`, `Artifacts`) scroll inside `.table-scroll`,
      not the page.
- [ ] Admin agent/workflow list + form pages — **out of scope**, confirmed
      absent from this repository by the plan/architecture passes; not
      addressed by this task.
- [x] Safe-area insets via `env(safe-area-inset-*)` on `.appbar`, `.tabbar`,
      `.task-detail__header`.
- [x] `api/` and `projection.py` import no drivers (unchanged import list;
      `test_api_imports_only_the_artifact_port_not_drivers` /
      `test_api_does_not_import_drivers` both pass).
- [x] Full test suite passes, including all named pinned test files.
