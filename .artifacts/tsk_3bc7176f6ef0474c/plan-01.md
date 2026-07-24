# Plan: fix mobile scrolling in the task detail dialog

## Summary
The board's task detail view is a native `<dialog id="detail">` (`src/harness/api/templates/board.html`,
populated by `_task.html`) that is never given an explicit height/overflow budget, and the page has no
viewport meta tag. On phone-sized viewports the dialog's content (task info, history, artifacts, live
output) overflows the visible area and there is no scroll container that lets the user reach the
artifacts table. This is a CSS/markup fix in the board's static template, not a backend change.

## Context
Reported bug: "unable to scroll down to artifacts on mobile view" in the task detail page. Looking at
the current markup:

- `board.html` has no `<meta name="viewport" content="width=device-width, initial-scale=1">`. Without
  it, mobile browsers lay the page out at a virtual desktop width (~980px) and the user sees a
  zoomed-out page rather than a proper responsive one — this alone can make touch scroll/zoom behavior
  on the dialog unreliable.
- The `dialog` rule (`board.html:23`) sets `max-width` and `width` but no `max-height` or `overflow`:
  ```css
  dialog { border: none; border-radius: 6px; padding: 16px; max-width: 640px; width: 90%; }
  ```
  Browser UA stylesheets differ in how much (if any) height budget and scrolling they give `<dialog>`
  by default, especially on mobile WebKit. With four stacked sections (info table, history table,
  artifacts table, live-output panel), content routinely exceeds the viewport height on a phone, and
  without an explicit `max-height` + `overflow-y: auto` on the dialog there is nothing scrollable to
  reach content below the fold — the artifacts section in particular sits between history and live
  output, so it's the first section to become unreachable.
- `_task.html:46-51` also has its own inner scroll box (`#stage-output`, `max-height: 240px; overflow:
  auto`) for live output, stacked below artifacts. A nested scrollable region inside a dialog that
  itself doesn't scroll is a classic mobile trap: touch drags starting outside that box do nothing,
  and the artifacts table above it stays out of reach.

Net cause is layout, not JS: the dialog needs a bounded height with its own vertical scroll, and the
page needs a viewport meta tag so mobile browsers use real viewport units instead of a virtual desktop
width.

## Functional requirements

**FR-1 — Add a viewport meta tag.**
`board.html`'s `<head>` gets `<meta name="viewport" content="width=device-width, initial-scale=1">`.
- Acceptance: the rendered `/` (board) HTML response contains this exact meta tag.

**FR-2 — The detail dialog is height-bounded and scrolls its own content.**
The `dialog` CSS rule gets an explicit `max-height` (e.g. `85vh`, or `85dvh` with a `vh` fallback for
older WebKit) and `overflow-y: auto`, so once content exceeds that height the dialog itself becomes the
scroll container instead of clipping/overflowing silently.
- Acceptance: the `dialog` rule in the served CSS includes both `max-height` and `overflow-y: auto` (or
  equivalent `overflow: auto`).
- Acceptance: opening a task with a long history + several artifacts + live output on a 375×667 (iPhone
  SE) and a 390×844 (iPhone 12/13) viewport, the user can touch-scroll from the top of the dialog all
  the way to the artifacts table and down to live output; nothing is clipped without a way to reach it.

**FR-3 — The nested live-output scroll box no longer blocks reaching content above it.**
Since `#stage-output` is already its own bounded, scrollable box (`max-height: 240px; overflow: auto`),
add `overscroll-behavior: contain` to it so a touch scroll that runs out of room inside that inner box
doesn't get "stuck" instead of continuing to scroll the outer dialog. (This only matters once FR-2 makes
the dialog itself scrollable — today there's no outer scroll for the gesture to fall back to.)
- Acceptance: with the dialog scrolled so `#stage-output` fills the viewport, continuing the scroll
  gesture past the bottom of `#stage-output` scrolls the dialog, not nothing.

**FR-4 — No regression on desktop.**
Desktop dialog presentation (centered, `max-width: 640px`, `width: 90%`) is unchanged in size/position;
only the height/overflow behavior is added.
- Acceptance: existing `test_api_html.py` assertions on rendered markup continue to pass unchanged
  (they assert on table/section content, not on the `<style>` block, so no test changes are expected
  there); a manual check at a desktop viewport (e.g. 1280×800) shows the same layout as before for
  short content, with the new max-height/scroll only kicking in once content is tall enough to need it.

## Non-functional requirements
- **No backend/route changes.** This is confined to `src/harness/api/templates/board.html` (and
  possibly a one-line tweak in `_task.html` for FR-3). No Python module in the dependency table changes,
  so `test_architecture.py` is unaffected.
- **No new JS.** The fix is CSS + one meta tag; it must not add scroll-locking JavaScript or a new
  client-side dependency, consistent with the project's minimal htmx-based frontend.
- **Works without JS fallback for the viewport tag** — a `<meta>` tag renders regardless of script
  execution, so this holds even if htmx/SSE scripts fail to load.

## Data model
No data model changes. This is a template/CSS-only fix; no new entities, fields, or persisted state.

## Interfaces
- `GET /` (board.html) — gains the viewport `<meta>` tag and the updated `dialog` CSS rule.
- `GET /fragment/board`, `GET /tasks/{id}` (task detail fragment, `_task.html`) — no structural HTML
  changes required beyond the optional `overscroll-behavior` tweak on `#stage-output`; the dialog that
  hosts this fragment is styled in the parent `board.html`.
- No API/route signatures change.

## Dependencies and scope
- Depends on: nothing outside `src/harness/api/templates/board.html` and `_task.html`.
- Out of scope:
  - Any redesign of the detail view's information architecture (reordering sections, collapsing
    tables, etc.) — this task is strictly about making existing content reachable by scroll.
  - Adding automated browser/visual tests (Playwright, etc.) — this project's test suite is
    FastAPI-`TestClient`-based (`tests/test_api_html.py` and friends) and asserts on rendered HTML
    strings, not on layout/rendering. There is no browser-automation harness in this repo to add a true
    cross-device visual regression test.
  - Any change to the SSE/htmx wiring in `board.html`'s `<script>` block.

## Rough plan
1. Add the viewport `<meta>` tag to `board.html`'s `<head>` (FR-1).
2. Update the `dialog` CSS rule in `board.html` to add `max-height` and `overflow-y: auto` (FR-2).
3. Add `overscroll-behavior: contain` to the `#stage-output` inline style in `_task.html` (FR-3).
4. Run `.venv/bin/pytest -q` — expect `test_api_html.py` and the rest of the suite to pass unchanged,
   since assertions target content, not the `<style>` block or viewport meta.
5. Manually verify in a real or emulated mobile viewport (browser devtools device toolbar at ~375×667
   and ~390×844, plus a quick desktop check at ~1280×800): open a task with several artifacts and a
   history long enough to overflow the dialog, confirm the whole dialog scrolls and the artifacts table
   is reachable, and that desktop presentation is unchanged for short content.
6. Since this is a pure template/CSS change with no `.py` behavior touched, no new unit test is
   strictly required; if the reviewer step wants a regression guard, the cheapest one is a
   `test_api_html.py` assertion that the served board HTML contains the viewport meta tag and that the
   `dialog` rule includes `max-height`/`overflow-y: auto` — a string-level check standing in for the
   missing visual-regression tooling.

## Open questions
- **`vh` vs `dvh`.** iOS Safari's address bar can change the effective viewport height, and `100vh`
  historically overshoots when the toolbar is showing. Default: use `max-height: 85vh` for broad
  compatibility, and layer `max-height: 85dvh` after it as a progressive enhancement (browsers that
  don't understand `dvh` ignore the second declaration and keep the `vh` value). Not blocking.
- **Should the detail view move from a centered dialog to a full-screen sheet on narrow viewports?**
  That would be a more thorough mobile-UX fix but is a bigger design change than "make artifacts
  reachable." Default: keep the dialog, just bound its height and let it scroll — note this as a
  possible follow-up, not part of this fix.
- **Automated regression coverage for the fix.** Given the repo has no browser-rendering test
  infrastructure, the acceptance criterion "mobile responsiveness is tested on various screen sizes"
  will be satisfied by manual verification at the viewport sizes listed above, plus an optional
  string-level assertion on the served CSS (see plan step 6). Flagging this now so review/architecture
  doesn't expect a Playwright-style test that this repo has no harness for.
