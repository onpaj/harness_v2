# Development: full-screen tabbed task detail view

Implemented per `plan-01.md` / `design-01.md` / `architecture-01.md`. Pure
template/CSS/inline-JS change, no backend/port/route changes.

## Files changed

- `src/harness/api/templates/board.html`
  - CSS: `dialog#detail` grown from `max-width: 640px; width: 90%` to a
    full-screen layout (`width: 96vw; height: 92vh; max-width: none; padding: 0`),
    `dialog[open] { display: flex; flex-direction: column }` so the dialog's
    UA-default `display: block` doesn't fight the internal flex layout.
    Added `.task-detail`/`.task-detail__header`/`.tabs`/`.tab`/`.tab-panels`/
    `.tab-panel` rules in the same `<style>` block (no new stylesheet): a
    fixed-height header and tab bar, with `.tab-panels` taking the remaining
    space (`flex: 1; min-height: 0; overflow: auto`) so the active panel
    (`.tab-panel.active`, itself `display: flex; flex-direction: column;
    flex: 1; min-height: 0`) fills the available height — this is what lets
    the Output tab's log div fill the tab instead of being capped at the old
    `max-height: 240px`.
  - JS: added two new delegated listeners on `#detail`, alongside the three
    that already existed (`afterSwap` → `showModal()`, backdrop-click →
    `close()`, `close` → clear `innerHTML`):
    - a click listener for the new `.task-detail__close` (✕) button, needed
      because at 96vw × 92vh there's only a thin backdrop margin left to
      click to dismiss the modal;
    - a click listener matching `.tab` buttons that toggles `.active` on the
      clicked tab/sibling tabs and on the matching `.tab-panel` (matched by
      `data-tab` === `data-panel`, a plain string match, not positional).
      Hiding is CSS-only (`.tab-panel:not(.active) { display: none }` via the
      base `.tab-panel` rule) — the Output panel's SSE-connected `<div>` is
      never detached from the DOM, so switching tabs never tears down or
      re-establishes the `EventSource`.
  - Both listeners are registered once in the page's static `<script>` block,
    not inside `_task.html` (which is destroyed/re-parsed on every open or
    restart), per the architecture review's explicit guardrail.

- `src/harness/api/templates/_task.html`
  - Restructured (content moved, not rewritten — same Jinja loops) into:
    `header.task-detail__header` (task id, conditional Restart button
    unchanged, new ✕ close button) → `nav.tabs` (three buttons, `data-tab`
    values `info`/`history`/`output`, first marked `active`) →
    `div.tab-panels` with three `section.tab-panel`s (`data-panel` matching
    values, first marked `active`):
    - **Task info** panel: the existing metadata table, plus the artifacts
      table (moved here from its own top-level `<h3>`, per FR-2/FR-5).
    - **History** panel: the existing history table, now given the full tab
      body instead of a small embedded block.
    - **Output** panel: the existing SSE `hx-ext="sse"` div/`#stage-output`,
      unchanged endpoint (`/api/tasks/{id}/output/events`) and unchanged
      escaping (server-side, `routes.py`), now sized to fill the tab
      (`flex: 1; min-height: 0`) instead of the old fixed `max-height: 240px`.
  - No `<script>` added to this file — stays pure markup, per the design's
    explicit boundary (listener lives in `board.html` only).

No changes to `routes.py`, `_columns.html`, or any port/driver file — the two
context variables (`task`, `artifacts`) passed into `_task.html` by
`_task_fragment` (`routes.py:115-123`) are unchanged, and the SSE endpoint
(`routes.py:158-163`) is unchanged.

## Verification

**Automated:** `.venv/bin/pytest -q` → 394 passed, 1 skipped (pre-existing
opt-in `HARNESS_SMOKE_CLAUDE` skip), 0 failed. `tests/test_api_html.py`'s
substring-only assertions (confirmed by reading the file before starting)
needed no changes — all pass unmodified against the new markup, including
`test_restart_button_shown_only_for_failed_task` (the restart button's
`{% if %}` guard moved into the new header without being duplicated
elsewhere).

**Manual (Playwright against a live `uvicorn` instance of the real app, not a
mock):** built a throwaway driver script instantiating `create_app` with
`FakeBoardView`/`FakeTaskControl` (from `tests/fakes.py`) plus small fixed
`ArtifactView`/`StageOutputView` fakes — one task with history + artifacts, a
`StageOutputView` that streams a new line every second so the SSE-survival
check has something live to observe. Verified in a real Chromium session
(screenshots taken, not just assertions) at both 1280px and 768px viewports:

1. Opening a task card shows the full-screen modal, defaulting to the
   **Task info** tab (satisfies FR-1/FR-3).
2. Clicking **Output**, waiting ~2.5s, switching to **History**, waiting
   another ~2.5s, then switching back to **Output** shows *more* lines than
   before the switch (SSE kept streaming while the tab was hidden) — the
   `#stage-output` element count stayed at 1 throughout (never removed and
   re-created), confirming FR-2's "no teardown/reconnect on tab switch."
3. The artifacts table renders under Task info with working download links
   (FR-5), unchanged route.
4. Opening a `failed` task shows the Restart button in the header; clicking
   it POSTs to `/tasks/{id}/restart`, the fragment re-renders, and the tab
   state resets to Task info (FR-6/FR-4) — confirmed via
   `FakeTaskControl.restarted` and re-reading the active tab after restart.
5. Clicking the new ✕ button closes the dialog (`open` attribute removed)
   and clears `innerHTML` (confirms the existing SSE-teardown-on-close
   behavior, `board.html`'s `close` listener, is undisturbed by the new
   close-button listener — they're two independent `addEventListener` calls,
   not merged).
6. At 768px viewport width the modal, tab bar and tables remain usable (no
   overflow/clipping) — the responsive floor named in the plan/design.

Screenshots taken during verification (not part of the repo, throwaway):
`/tmp/shot_1_info_tab.png`, `/tmp/shot_2_output_tab.png`,
`/tmp/shot_5_failed_task.png`, `/tmp/shot_7_768w.png` and others — all show
the expected full-screen tabbed layout.

**To re-verify manually:** `harness init && harness run` (or point
`create_app` at real drivers) and open any task on the board; click through
Task info / History / Output, confirm Output keeps streaming after switching
away and back, confirm Restart on a failed task, confirm the ✕/backdrop/Esc
all close the modal and stop the `/api/tasks/{id}/output/events` connection
(check devtools' Network tab).

## Notes / deviations from the design docs

None. Implemented exactly as specified: single-file `_task.html` (no
partial split, per the design's default), server-rendered active tab (no
client-side "remember last tab" state), CSS-only tab hiding, two separate
new listeners rather than one merged dispatch handler — matching the
architecture review's guardrails point for point.
