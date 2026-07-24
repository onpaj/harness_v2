# Merge conflict resolution — PR #114

## Conflict

Merging `origin/main` into this branch conflicted in two files:

- `src/harness/api/templates/board.html`
- `src/harness/api/templates/_task.html`

This branch's own change (from `plan-01.md`/`design-01.md`/`architecture-01.md`)
was a minimal CSS-only fix for a mobile scroll bug: a viewport meta tag, a
bounded/scrollable `<dialog>` (inline `<style>` block, `max-height: 85vh`/`85dvh` +
`overflow-y: auto`), and `overscroll-behavior: contain` on the `#stage-output`
live-output div.

Meanwhile `origin/main` had landed a much larger, unrelated mobile redesign of
the same templates in between: a full external stylesheet (`app.css`) replacing
the inline `<style>` block, richer viewport/theme-color/apple-mobile-web-app meta
tags, and a full-screen-sheet-on-mobile / centered-dialog-on-desktop treatment for
`dialog#detail` (`max-height: 100dvh` on mobile, a `768px` breakpoint on desktop),
plus a tabbed Info/History/Output task-detail layout where the output panel's
`#stage-output` now carries a shared `.terminal` class instead of an inline style.

## Resolution

`origin/main`'s redesign already fully and more thoroughly solves the mobile
dialog-scrolling problem this branch set out to fix (the dialog is bounded and
scrollable on every viewport, not just via a fixed `85vh`/`85dvh` cap), so it
subsumes this branch's dialog/meta-tag changes. Resolved both files by taking
`origin/main`'s side entirely — external stylesheet, full meta tag set, tabbed
task-detail markup.

One piece of this branch's intent was **not** already covered by `origin/main`:
`overscroll-behavior: contain` on the live-output scroll region, which stops
scroll chaining into the page behind it while scrolling long agent output on
mobile. Ported that forward onto the `.terminal` rule in `app.css` (the class
`origin/main`'s redesign now uses for `#stage-output`), since it now lives in the
external stylesheet rather than inline.

Updated the regression test this branch had added
(`test_index_has_viewport_meta_and_scrollable_dialog` in `tests/test_api_html.py`)
to assert against the merged reality instead of the superseded inline-`<style>`
shape: the richer viewport meta content, the `dialog#detail` bounding rule and
the `.terminal` rule's `overflow: auto` + `overscroll-behavior: contain`, read via
`/static/app.css` through the test client (matching the pattern already used in
`tests/test_api_html_mobile.py`, `origin/main`'s own test file for this redesign).

## Verification

Full suite: `.venv/bin/pytest -q` → **1318 passed, 1 skipped**, no failures.
No conflict markers remain anywhere in the tree.
