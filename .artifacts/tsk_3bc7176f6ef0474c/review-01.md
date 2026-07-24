# Review: fix mobile scrolling in the task detail dialog

## Verdict: done

## Checked

- **Conformance to spec (FR-1..FR-4, acceptance criteria).**
  - FR-1: `<meta name="viewport" content="width=device-width, initial-scale=1">`
    added to `board.html`'s `<head>`, right after the charset meta — verified in
    the diff and confirmed present in the live `/` response.
  - FR-2: `dialog` rule extended with `max-height: 85vh; max-height: 85dvh;
    overflow-y: auto;` — matches the `vh`-with-`dvh`-fallback technique agreed in
    design/architecture. Only one `dialog {` rule exists in the file, so this is
    unambiguous.
  - FR-3: `#stage-output` inline style gains `overscroll-behavior: contain`
    alongside its existing `max-height: 240px; overflow: auto`.
  - FR-4: desktop `max-width`/`width` declarations on `dialog` are untouched;
    `max-height`/`overflow-y` only constrain, they don't force a height, so
    short-content desktop presentation is unaffected.
  - All three changes are exactly where plan-01.md/design-01.md/architecture-01.md
    specified, with the exact CSS values proposed — no drift between artifacts and
    implementation.

- **Adherence to architecture.** Change is confined to two Jinja templates under
  `src/harness/api/templates/` plus one new test; no Python module in the
  dependency table is touched, so none of the CLAUDE.md invariants (which are all
  about `dispatcher`/`consumer`/`router`/ports/drivers) are implicated. Confirmed
  no import-graph changes.

- **Completeness / required tests.** Architecture step explicitly required a
  `tests/test_api_html.py` regression check for the viewport meta and dialog
  overflow CSS; `test_index_has_viewport_meta_and_scrollable_dialog` was added and
  asserts both. Full suite run locally: **473 passed, 1 skipped** (the skip is the
  opt-in real-`claude` smoke test, expected without `HARNESS_SMOKE_CLAUDE=1`) —
  matches development-01.md's reported result.

- **Correctness.** CSS is valid (duplicate `max-height` declaration is a standard,
  browser-supported progressive-enhancement pattern — not a bug). No logic, no
  new JS, no backend/route changes, consistent with the plan's non-functional
  requirements. The one acceptance sub-item that can't be machine-verified in this
  repo — actual touch-scroll behavior on real mobile viewports — was explicitly
  scoped out at the plan stage (no browser-automation harness exists here) and
  flagged consistently across all four artifacts; not a gap introduced by this
  step.

No issues found. Implementation is a minimal, faithful realization of the
approved design with adequate regression coverage.
