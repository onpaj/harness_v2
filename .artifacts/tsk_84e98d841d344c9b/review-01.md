# Review: full-screen tabbed task detail view

Reviewed the implementation (`ca7d49d`) against `plan-01.md`, `design-01.md`
and `architecture-01.md`, by reading the actual diff to `board.html` and
`_task.html` (not just trusting `development-01.md`'s claims) and running the
test suite.

## Spec conformance

- **FR-1 (full-screen modal):** `board.html:23-24` — `dialog { max-width:
  none; width: 96vw; height: 92vh; }` + `dialog[open] { display: flex;
  flex-direction: column; }`. Matches design exactly. Open/close mechanics
  (`afterSwap` → `showModal()`, backdrop click → `close()`) unchanged.
- **FR-2 (three tabs, no re-fetch, SSE survives switching):** `_task.html`
  has `nav.tabs` with three `data-tab` buttons and `div.tab-panels` with
  three `section[data-panel]`s. Hiding is CSS-only —
  `.tab-panel { display: none; }` / `.tab-panel.active { display: flex; ... }`
  (`board.html:42-43`) — the SSE div (`hx-ext="sse"` in the Output panel) is
  never removed from the DOM by the tab-click listener
  (`board.html:65-75`), which only toggles the `active` class via string
  match on `data-tab`/`data-panel`. This is the one place a shortcut could
  have silently broken the design's core guarantee, and it wasn't taken.
- **FR-3/FR-4 (default tab, no leaked state):** server renders `class="tab
  active"` / `class="tab-panel active"` on the info tab/panel only
  (`_task.html:13,19`); since the fragment is fully re-rendered on every
  open/restart, there is no client-side "remember last tab" state to leak —
  matches the architecture review's explicit recommendation.
- **FR-5 (artifacts linkable):** artifacts table moved under the Task info
  panel (`_task.html:31-41`), same route (`/api/tasks/{id}/artifacts/...`),
  unchanged.
- **FR-6 (restart):** restart button's `{% if task.status == "failed" %}`
  guard moved into the header, not duplicated elsewhere; `hx-post`/`hx-target`
  unchanged (`_task.html:4-8`).

## Architecture / invariants

- No `routes.py`, port, or driver touched — confirmed by diff stat (only
  `board.html` + `_task.html` changed, plus the artifact file). Invariant 5
  (api/projection never import drivers) not at risk here; this change doesn't
  even approach that boundary.
- Tab-click and close-button listeners are registered once in `board.html`'s
  static `<script>`, delegated off `#detail`, not inside `_task.html` (which
  has no `<script>` at all) — matches the architecture review's guardrail
  against duplicate-listener registration on every fragment swap.
- Escaping: `routes.py:67-68` still does `html.escape(...)` server-side for
  SSE lines before the client renders them; no `|safe` filter anywhere in
  either template. The redesign didn't introduce an unescaped-output path.

## Verification

- `.venv/bin/pytest -q` → 394 passed, 1 skipped (pre-existing opt-in smoke),
  0 failed. Confirmed `test_restart_button_shown_only_for_failed_task` and
  the other `test_api_html.py` substring assertions pass unmodified against
  the new markup, as design-01.md predicted.
- Read the actual rendered `_task.html`/`board.html` source directly (not
  just the development step's narrative) — the structure, CSS and JS match
  what's documented in `development-01.md` point for point: full-screen
  dialog CSS, tab bar, three panels, CSS-only hiding, single delegated
  listener set, server-rendered default-active tab.

## Verdict

Implementation matches the plan/design/architecture with no deviations, no
invariant violations, and no correctness bugs found in the tab-switching /
SSE-survival logic (the one real risk area called out by the architecture
review). Manual Playwright verification is documented in
`development-01.md`; not independently re-run here, but the static
review of the markup/JS is sufficient to confirm the CSS-only hiding
mechanism that verification was meant to check.

```json
{"outcome": "done", "summary": "Implementation matches plan/design/architecture: full-screen modal, three CSS-only tabs, SSE survives switching, restart/artifacts unchanged, no backend/port changes, 394 tests pass."}
```
