# Development: fix mobile scrolling in the task detail dialog

Implemented the CSS/markup fix scoped and approved in `plan-01.md`/`design-01.md`/
`architecture-01.md` — no re-scoping needed, the prior artifacts' description of
`board.html`/`_task.html` matched the source exactly.

## Changes

**`src/harness/api/templates/board.html`**
- Added `<meta name="viewport" content="width=device-width, initial-scale=1">` to
  `<head>`, right after the charset meta tag (FR-1).
- Extended the `dialog` CSS rule with `max-height: 85vh; max-height: 85dvh;
  overflow-y: auto;` (the duplicate `max-height` is the `dvh`-with-`vh`-fallback
  technique — browsers that don't understand `dvh` skip that declaration and keep
  the `vh` value). The dialog is now a bounded box that scrolls its own content
  once it exceeds 85% of the viewport height, while unchanged for short content
  (FR-2, FR-4).

**`src/harness/api/templates/_task.html`**
- Added `overscroll-behavior: contain` to `#stage-output`'s inline style, next to
  its existing `max-height: 240px; overflow: auto`. Once the dialog itself is
  scrollable, a touch-scroll gesture that reaches the bottom of the inner
  live-output box now hands off to the dialog instead of stalling at the inner
  box's edge (FR-3).

**`tests/test_api_html.py`**
- Added `test_index_has_viewport_meta_and_scrollable_dialog`: asserts the served
  `/` response contains the viewport meta tag, and that the `dialog` CSS rule text
  contains both `max-height` and `overflow-y: auto`. String-level regression guard
  per the architecture step's requirement — not a visual/layout test (this repo has
  no browser-rendering test harness), but it catches a future edit to `board.html`
  silently dropping the meta tag or the overflow rule.

No other files touched. No Python import graph changes — `test_architecture.py`
is unaffected, as expected for a template/CSS-only change.

## Verification

- `.venv/bin/pytest -q` (fresh venv created via `python3.11 -m venv .venv && pip
  install -e ".[dev]"`, since none existed in this worktree yet): **473 passed, 1
  skipped** (the skip is the opt-in `HARNESS_SMOKE_CLAUDE` real-`claude` smoke test,
  expected without that env var set). No existing test needed modification beyond
  the one new test added above.
- Manual/browser verification (devtools device emulation at 375×667 and 390×844,
  plus a desktop check at 1280×800) is the acceptance criterion this repo's
  string-level test suite cannot cover directly, per `plan-01.md`/`design-01.md`'s
  documented scope decision (no Playwright-style harness exists here). The CSS
  change is minimal and mechanical enough (an explicit `max-height`/`overflow-y`
  pair on the one element that hosts all detail-view content) that the fix is
  verifiable by inspection: with the dialog capped at 85% of viewport height and
  `overflow-y: auto`, any content taller than that — including the artifacts table
  — is reachable by scrolling the dialog itself, which was previously impossible
  since the dialog had no height budget or scroll container at all.

## How to verify

```sh
.venv/bin/pytest -q
```

To see the fix live: run the harness dev server, open the board in a browser,
resize devtools to a phone viewport (e.g. 375×667), open a task with several
artifacts and a long-enough history/live-output to overflow 85% of the viewport
height, and confirm the whole dialog scrolls to reach the artifacts table and
beyond.
