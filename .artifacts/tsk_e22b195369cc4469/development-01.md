# Development — Harness dashboard: local-timezone timestamps

Implements `plan-01.md` / `design-01.md`, per `architecture-01.md`'s approval
(with its one required test addition). Purely client-side presentation
change — no route, port, or projection code touched.

## Files changed

- **New**: `src/harness/api/static/format-local-time.js` — vendored flat
  next to `htmx.min.js`/`sse.js`, served by the existing `/static`
  `StaticFiles` mount (`app.py`). Exports `window.localizeTimes(root?)`:
  scans `root` (default `document`) for `time[datetime]` elements and
  rewrites each node's text to an `Intl.DateTimeFormat(undefined,
  {dateStyle:'medium', timeStyle:'medium', timeZoneName:'short'})`-formatted
  local rendering of its `datetime` attribute. Wrapped per-node in
  `try/catch` (plus an `isNaN` guard on the parsed `Date`) so a malformed or
  unexpected timestamp shape leaves the server-rendered raw-UTC fallback
  text in place rather than breaking the page.
- **`src/harness/api/templates/_columns.html`** — the card's "since" line
  (`task.history[-1].at`) is now wrapped in `<time datetime="..."
  title="... UTC">...</time>` instead of bare text. Raw UTC string is
  unchanged as `datetime`, `title`, and initial text content.
- **`src/harness/api/templates/_task.html`** — same treatment for the detail
  dialog's "created" row (`task.created`) and the history table's "time"
  column (`entry.at`, inside the `{% for %}` loop). No other row changed
  (`status`, `lastOutcome`, `lockId`, `outcome`, `actor` stay bare text, as
  specified).
- **`src/harness/api/templates/board.html`** — wiring only:
  - Added `<script src="/static/format-local-time.js"></script>` after
    `sse.js`, before the inline `<script>` block.
  - Inline block now calls `localizeTimes();` as its first statement (covers
    the initial server-rendered `#board` content from `{% include
    "_columns.html" %}`).
  - The existing `htmx:afterSwap` listener now runs
    `localizeTimes(event.detail.target);` as its first statement, ahead of
    the pre-existing `showModal()` check — so it fires unconditionally for
    every swap target: the SSE-driven `#board` refresh, opening the `#detail`
    dialog, and the restart button's `#detail` re-swap. No new event
    listener was added; the revision-dedup IIFE is untouched.
- **`tests/test_api_html.py`** — added the architecture review's required
  markup-contract coverage (server-side, via `TestClient`; no JS execution):
  - `test_index_renders_board_shell` now also asserts
    `/static/format-local-time.js` is referenced in the page shell.
  - `test_static_files_are_served` now also asserts
    `/static/format-local-time.js` returns 200.
  - New `test_card_time_is_a_time_element_for_client_side_localization`:
    asserts the card's "since" `<time datetime="..." title="... UTC">`
    markup.
  - New `test_fragment_task_times_are_time_elements_for_client_side_
    localization`: asserts both the "created" field and the history table's
    time column render as `<time datetime="..." title="... UTC">`.
  - All pre-existing raw-UTC-substring assertions (e.g. `assert
    "2026-07-19T10:00:05Z" in body`) were left untouched and still pass
    unmodified, since the raw string remains the element's `datetime`,
    `title`, and initial text content — exactly as the architecture review
    required.

## What was intentionally not changed

- No backend/route/port/projection code. `Task.created`, `HistoryEntry.at`,
  the on-disk queue JSON, and the `/api/board` / `/api/tasks/{id}` JSON API
  all keep emitting UTC ISO-8601 strings unchanged.
- No new dependency, no build step — `format-local-time.js` is a plain
  vendored script matching the existing `htmx.min.js`/`sse.js` pattern.
- No timezone override/picker UI — the browser's system timezone (via
  `Intl`) is authoritative, per the acceptance criteria.

## How to verify

Automated (server-side markup contract):
```sh
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```
Result: 474 passed, 1 skipped (the opt-in `HARNESS_SMOKE_CLAUDE=1` smoke
test), 0 failed — includes the 4 new/extended assertions above plus all
pre-existing raw-UTC-string assertions passing unmodified.

Manual (client-side conversion — this repo has no JS test runner, so this
is the verification boundary the architecture review specified):
```sh
harness run   # or however the board is normally started locally
```
Then in a browser with devtools open:
1. Load the board — cards' "since" line and any detail dialog you open
   should show a local wall-clock time with a trailing zone indicator
   (e.g. `Jul 23, 2026, 2:00:00 PM GMT-4`), not the raw
   `2026-07-23T18:00:00Z` string.
2. Hover a timestamp — the native tooltip (`title`) shows the exact UTC
   instant.
3. Trigger a live SSE board refresh (e.g. let a task move columns) — the
   newly swapped cards should already show local time, no UTC flash.
4. Open a task's detail dialog — "created" and every history row's "time"
   column show local time.
5. Restart a failed task — the re-swapped detail dialog still shows local
   time.
6. Override the browser/OS timezone (devtools sensors panel or OS setting)
   and reload — displayed times shift accordingly with no server
   round-trip.
7. Spot-check a DST-transition instant in a DST-observing zone (e.g.
   `America/New_York`) — `Intl.DateTimeFormat` resolves real timezone rules,
   not a fixed offset, so pre/post-transition instants render with the
   correct offset.

## Verdict

All acceptance criteria are met by this change:
- Timestamps render in local time (FR-1).
- The zone is visible via `timeZoneName: 'short'`, with the absolute UTC
  instant recoverable via `title` (FR-2).
- Conversion re-applies after every dashboard refresh path via the single
  `htmx:afterSwap` chokepoint (FR-3).
- DST/offset correctness comes from `Intl`/`Date` resolving real timezone
  rules rather than a static offset (FR-4).
- Only standard `Date`/`Intl.DateTimeFormat` APIs are used — no polyfill,
  no UA sniffing (FR-5).
