# Plan: Copy button for task IDs in the board UI

## Summary
The task detail panel (`_task.html`, rendered into the `<dialog id="detail">` on
`board.html`) currently shows the task ID only as a plain `<h2>{{ task.id }}</h2>`
heading — there is no way to copy it. This task adds a small copy-icon button next
to the ID that copies it to the clipboard via `navigator.clipboard.writeText()` and
flips to a checkmark for 1.5s as confirmation, styled to match the existing board
look (and to degrade sensibly under `prefers-color-scheme: dark`, which the board
does not currently define at all).

## Context
Users read task IDs off the board to reference them elsewhere (issue comments,
`harness restart`/CLI lookups, Slack messages to teammates). Retyping a
`tsk_xxxxxxxxxxxxxxxx` id by hand is error-prone. This is a pure front-end
enhancement to `src/harness/api/templates/`; it touches no ports, no drivers, no
routing — invariant #5 (`api/` never imports `drivers/`) is unaffected since
nothing here talks to a driver at all.

## Functional requirements

**FR-1 — Copy icon is rendered next to the task ID.**
- The `<h2>{{ task.id }}</h2>` in `_task.html` is followed by a `<button>` element
  (type="button", not inside any `<form>`) showing a clipboard glyph (📋).
- Acceptance: `GET /fragment/task/{id}` response body contains a button element
  with a `data-copy-id="{{ task.id }}"` attribute (or equivalent hook the JS can
  bind to) inside the task detail markup.

**FR-2 — Clicking the icon copies the task ID.**
- On click, the button's handler calls `navigator.clipboard.writeText(id)` with
  the exact task id string (no surrounding whitespace/markup).
- Acceptance: a test driving the rendered HTML/JS (or a focused DOM test) confirms
  the handler is wired to call `navigator.clipboard.writeText` with the task's id
  argument. Because task detail markup is swapped in repeatedly via htmx
  (`hx-target="#detail" hx-swap="innerHTML"`), the binding must use event
  delegation from a node that survives swaps (e.g. listen on `document` or on the
  `#detail` dialog itself, filtering on a `data-copy-id` attribute) rather than
  attaching a listener to the button at load time — the button does not exist at
  page load.

**FR-3 — Visual confirmation for 1.5s.**
- On a successful copy, the icon's content/class switches to a checkmark (✓) and
  reverts to 📋 automatically after 1500ms.
- If a second click happens while the confirmation is still showing, the timer
  resets cleanly (no flicker, no leaked stacked timeouts pushing the revert later
  than 1.5s after the *last* click).
- Acceptance: after simulating a click, the button's text/state is ✓; after
  1.5s (fake timers) it is back to 📋.

**FR-4 — Consistent styling.**
- The button is styled with the board's existing visual language (system font,
  the same border-radius/spacing scale used elsewhere — e.g. `.card`, `dialog`),
  not a bare unstyled `<button>`.
- Hover and active states are visually distinct from the resting state (e.g.
  background/opacity shift), consistent with how `.card` and other interactive
  elements in `board.html` already imply affordance via `cursor: pointer`.

**FR-5 — Works in light and dark themes.**
- The button (and its hover/active states, and the ✓ confirmation) remain legible
  against both the current light background (`#f4f5f7`/`#fff`) and a dark
  background under `@media (prefers-color-scheme: dark)`.
- Acceptance: manual/visual check with the OS/browser forced into each color
  scheme; no hard-coded color pairing that becomes invisible (e.g. light-gray
  icon on light-gray hover background) in either mode.

## Non-functional requirements
- **No new dependencies.** Use the Clipboard API and vanilla JS already present
  in `board.html`'s inline `<script>` — no new `<script src>`, no build step
  (there is none in this project; templates are hand-written Jinja + inline JS).
- **Graceful failure.** `navigator.clipboard.writeText()` returns a promise and
  can reject (e.g. insecure context, permission denied). A rejection must not
  throw an unhandled error to the console in a way that breaks the page; it's
  acceptable to just not show the ✓ confirmation on failure (no error UI is
  required by the acceptance criteria).
- **No server round-trip.** This is 100% client-side; no new endpoint, no change
  to `board.py`/routes, no change to `projection.py` or any port.
- **Survives htmx swaps.** Because `#detail`'s `innerHTML` is replaced every time
  a card is opened (and cleared on dialog close per the existing `close` handler
  in `board.html`), the click-handling script must not rely on a listener
  attached directly to the button node — see FR-2.

## Data model
No data model changes. No new entities, no changes to `Task`, `BoardView`, or any
projection field. The task id already exists as `task.id` in the Jinja context
passed to `_task.html` (see `api/` fragment route backing `/fragment/task/{id}`).

## Interfaces
- **Template change:** `src/harness/api/templates/_task.html` — add the button
  markup next to `<h2>{{ task.id }}</h2>`.
- **Script change:** `src/harness/api/templates/board.html` — extend the existing
  inline `<script>` block (alongside the htmx `afterSwap`/`close` listeners
  already there) with a delegated click handler and a small revert-after-1.5s
  helper.
- **Style change:** `board.html`'s existing `<style>` block gets a new rule set
  for the copy button (base/hover/active + a `prefers-color-scheme: dark` block).
- No HTTP endpoint, no event, no CLI surface changes.

## Dependencies and scope
- Depends on nothing beyond the existing `_task.html`/`board.html` templates and
  the browser's native Clipboard API.
- **In scope:** button markup, click-to-copy behavior, 1.5s confirmation state,
  styling for light/dark.
- **Out of scope:**
  - A general dark-mode overhaul of the board (today the board has *no*
    `prefers-color-scheme` handling at all — see Open Questions). Only the new
    button gets explicit dark-mode styling; the rest of the board's appearance is
    unchanged.
  - Copy buttons for any other field (repository, worktree, etc.) — only the
    task id, per the acceptance criteria.
  - Toast/snackbar notifications, sound, or any confirmation beyond the icon
    flip.
  - Any change to the `/fragment/task/{id}` route handler itself (`api/`
    Python code) — this is template/script/style only.

## Rough plan
1. **Markup:** in `_task.html`, wrap the id heading and a new button in a small
   flex container; give the button `type="button"`, `class="copy-id-btn"`,
   `data-copy-id="{{ task.id }}"`, `aria-label="Copy task id"`, initial content
   `📋`.
2. **Styling:** in `board.html`'s `<style>`, add `.copy-id-btn` (base look
   matching the system's border-radius/font scale) plus `:hover`/`:active`
   states, and a `@media (prefers-color-scheme: dark)` block covering the new
   button's colors only.
3. **Behavior:** in `board.html`'s existing inline `<script>`, add a single
   delegated `click` listener (e.g. on `document` or `#detail`) that:
   - matches `event.target.closest('.copy-id-btn')`,
   - calls `navigator.clipboard.writeText(button.dataset.copyId)`,
   - on success, swaps the button's text to `✓`, clears any pending revert
     timeout for that button, and sets a new one to restore `📋` after 1500ms.
4. **Tests:** extend `tests/test_api_html.py` (pattern already established there,
   e.g. `test_fragment_task_shows_metadata_and_history`) with an assertion that
   `/fragment/task/{id}` includes the copy button and `data-copy-id` with the
   correct id. If there's an existing JS/DOM test harness in the repo, add a
   focused test for the click → `writeText` → revert-after-1.5s behavior; if
   none exists, note this as a manual verification step (see Open Questions).
5. **Manual verification:** open the board, click a card, click the copy icon,
   confirm clipboard contents and the ✓→📋 flip, in both a light and a forced
   dark color scheme.

## Open questions
- **No dark-mode precedent exists in this codebase today** (`board.html`'s
  styles are all fixed light-theme colors, no `prefers-color-scheme` anywhere).
  Default taken: only the new button gets a dark-mode-aware rule; the rest of
  the board is left as-is, since a full dark-mode redesign is not part of this
  task's acceptance criteria and would be a much larger, separate effort.
- **"Kv (key-value) list" vs. the actual markup:** the task notes describe the
  button as living "in the kv list next to the task ID value," but the id is
  currently rendered as a bare `<h2>{{ task.id }}</h2>` heading, not a row in the
  `<table>` of key/value pairs below it (that table has no "id" row at all —
  it's implied by the heading). Default taken: place the button next to the
  `<h2>` heading, since that is the only place the id currently appears; adding
  a redundant "id" row to the table is out of scope unless a later step decides
  otherwise.
- **No existing JS test harness found** in `tests/` for the inline `<script>` in
  `board.html` (existing tests in `test_api_html.py` assert on rendered HTML
  strings via the FastAPI test client, not on JS behavior). Default taken: cover
  the markup/wiring (button present, correct `data-copy-id`) with a Python/HTML
  assertion test, and treat the click/clipboard/timer behavior itself as a
  manual verification step during development, unless the architecture step
  finds an existing JS-testing convention to reuse.
