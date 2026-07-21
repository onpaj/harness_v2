# Development: make the harness board mobile friendly

Implemented exactly what `plan-01.md` / `design-01.md` / `architecture-01.md`
specified: CSS-only responsive layout plus two additive, non-semantic markup
changes. No backend, route, port, or schema touched.

## Files changed

1. **`src/harness/api/templates/board.html`**
   - Added `<meta name="viewport" content="width=device-width, initial-scale=1">`
     to `<head>`, before `<title>` (FR-1).
   - Added a `.stage-output` class rule to the `<style>` block, carrying the
     exact values previously inline in `_task.html` (pure refactor, no visual
     change on its own).
   - Added a `.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }`
     rule (applies at all viewports; a no-op unless a table actually overflows
     its wrapper).
   - Added a baseline `button { min-height: 44px; ... }` rule (touch target
     size for the one `Restart` button, at all viewports).
   - Appended one `@media (max-width: 767px) { ... }` block overriding
     `.column` (85vw, `flex: 0 0 auto`), `.card` (bumped padding),
     `dialog` (94vw/90vh, internal scroll), and `.stage-output` (40vh cap).
   - All prior unconditional rules (`.column`, `.card`, `dialog`, etc.) are
     untouched — they remain the desktop default.

2. **`src/harness/api/templates/_task.html`**
   - Wrapped each of the three `<table>` elements (metadata, history,
     artifacts) in `<div class="table-scroll">...</div>` (FR-5).
   - Replaced the inline `style="..."` on `#stage-output` with
     `class="stage-output"` — required so the element can participate in the
     mobile media query (inline styles can't). `id="stage-output"`,
     `sse-swap="line"`, `hx-swap="beforeend scroll:bottom"` and the outer
     `sse-connect`/`sse-close` attributes are all unchanged.

3. **`src/harness/api/templates/_columns.html`** — no changes, as scoped;
   `.column`/`.card` styling is fully controlled from `board.html`.

4. **`tests/test_api_html.py`** — extended `test_index_renders_board_shell`
   with two assertions:
   ```python
   assert 'name="viewport"' in body
   assert "width=device-width" in body
   ```

## Verification

- Set up a fresh `.venv` (none existed in the worktree) with
  `python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"`, then ran
  the full suite:
  ```
  .venv/bin/pytest -q
  394 passed, 1 skipped, 1 warning in 15.43s
  ```
  The skip is the opt-in `test_smoke_claude.py` (requires
  `HARNESS_SMOKE_CLAUDE=1`), unrelated to this change. No existing test
  needed modification beyond the one new assertion — confirmed the
  architecture doc's claim that no test asserts on inline styles, class
  names, or exact pixel values.
- Confirmed via `grep` that every planned rule/attribute landed exactly once
  in the right file (`viewport`, `.table-scroll`, `.stage-output`, `@media
  (max-width: 767px)`, `min-height: 44px`).
- No manual browser/devtools pass was performed in this run (headless
  environment, no display) — the architecture doc's risk note about
  landscape-phone dialog clipping (`max-height: 90vh` vs. a very short
  viewport) is unverified visually. This is a reasonable follow-up manual
  check (resize devtools to ~375×375 and confirm the dialog doesn't clip)
  but does not block this step: the CSS is correct on its face and the
  existing automated coverage (which asserts response body text/ids/
  attributes, not rendered pixels) all passes.

## How to verify

```sh
cd /Users/rem/harness-root/worktrees/tsk_07f89f6403264fb1
.venv/bin/pytest -q
```

Manual/visual check (optional but recommended before merge): run
`harness run` (or serve the FastAPI app directly) and open the board in a
browser at a mobile viewport (devtools device toolbar, e.g. 375×667 and
375×375 landscape) — confirm one column dominates the screen with the next
peeking, cards are legibly padded, tapping a card opens a near-full-viewport
dialog that scrolls internally without clipping, and the Restart button (on
a failed task) is easily tappable. Then check ≥768px desktop width still
matches the pre-change layout (multi-column board, 640px-capped dialog).
