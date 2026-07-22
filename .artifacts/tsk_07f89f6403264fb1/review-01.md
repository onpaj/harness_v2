# Review: make the harness board mobile friendly

## Verdict: done

## What I checked

Read `plan-01.md`, `design-01.md`, `architecture-01.md`, `development-01.md`,
then verified the actual diff in commit `e1ee1db` against all four, and ran
the test suite myself rather than trusting the development doc's claim.

- **Diff scope matches exactly what was planned.** Only
  `src/harness/api/templates/board.html`, `src/harness/api/templates/_task.html`,
  and `tests/test_api_html.py` changed. No route, port, driver, or schema file
  touched — confirmed via `git show e1ee1db --stat`. `_columns.html` has zero
  changes, consistent with the design's claim that column/card sizing is
  fully controlled from `board.html`'s `<style>` block.
- **Every FR is implemented as specified:**
  - FR-1: viewport meta tag added to `<head>`, before `<title>`.
  - FR-2: `.column` mobile override (`min-width: 85vw; flex: 0 0 auto`).
  - FR-3/FR-7: baseline `button { min-height: 44px; ... }` rule plus `.card`
    mobile padding bump.
  - FR-4: `dialog` mobile override (94vw/90vh, internal scroll).
  - FR-5: all three `<table>`s in `_task.html` wrapped in
    `<div class="table-scroll">`.
  - FR-6: `.stage-output`'s inline style correctly extracted to a class (required
    so it can participate in the media query — inline styles can't), with the
    mobile `max-height: 40vh` override.
- **Contracts preserved.** `id="stage-output"`, `sse-swap`, `hx-swap`,
  `hx-post`/`hx-target` on Restart, and `sse-connect`/`sse-close` are byte-for-
  byte unchanged — verified directly in the diff, not just claimed.
- **Test addition matches architecture guidance exactly**: two assertions
  added to the existing `test_index_renders_board_shell`, no existing test
  restructured.
- **Ran `.venv/bin/pytest -q` myself**: `394 passed, 1 skipped, 1 warning in
  13.38s` — matches the development doc's claim exactly (the skip is the
  opt-in `HARNESS_SMOKE_CLAUDE=1` smoke test, unrelated).
- **No CLAUDE.md invariant is at risk.** This change touches only
  `api/templates/*.html`; none of `router.py`, `dispatcher.py`, `consumer.py`,
  `ports/`, `drivers/`, or `api/routes.py` are touched, so none of the 23
  invariants or `test_architecture.py`'s import-boundary checks apply.

## Non-blocking notes (not requesting changes)

- The architecture doc's own risk — `dialog` clipping on a very short
  landscape viewport (~375×375) — was never manually verified (headless dev
  environment, no display, as the development doc honestly discloses). This
  is flagged as a follow-up manual check in both the architecture and
  development docs, not a blocking gap: the plan explicitly scoped
  verification as "manual (browser devtools device emulation)" with no
  automated visual regression tooling in this repo, so there's no automated
  way to close this gap in this step. Worth a real device/devtools check
  before relying on this in practice, but it doesn't block landing a
  presentation-only, CSS-additive change with full test coverage passing.
- The global `button { min-height: 44px; ... }` selector is a landmine for a
  future second button with different sizing needs — already flagged as an
  accepted, documented tradeoff in the architecture doc's risks section, not
  a defect in this change.

```json
{"outcome": "done", "summary": "Implementation matches plan/design/architecture exactly: viewport meta, one mobile media query for .column/.card/dialog/.stage-output, .table-scroll wrapper on all three _task.html tables, 44px button baseline, inline style correctly extracted to a class so it can participate in the media query. Diff scope is exactly the three files specified, all hx-/sse-/id contracts preserved. Verified the full suite myself: 394 passed, 1 skipped, matching the development doc's claim. No CLAUDE.md invariant is implicated (template/CSS-only). Only gap is an unperformed manual device-emulation check for landscape dialog clipping, already flagged as a known follow-up in the architecture doc, not a defect."}
```
