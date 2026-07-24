# Architecture assessment — Harness dashboard: local-timezone timestamps

Reviewed against `plan-01.md`, `design-01.md`, and the current state of
`src/harness/api/` and `tests/test_api_html.py`. **Verdict: approve the design
as written, with one required addition (a static-behavior test) and one
clarified constraint (exact wiring point in `board.html`).** No changes to the
proposed component boundaries are needed.

## Alignment with existing patterns and integration points

Verified directly against the repo, not just the plan's description:

- `src/harness/api/static/` already serves flat, unbundled vendor files
  (`htmx.min.js`, `sse.js`) via `StaticFiles` mounted at `/static` in
  `app.py:73-75`. Dropping `format-local-time.js` next to them, loaded with a
  plain `<script src="...">` tag, matches the existing pattern exactly — no
  build step is introduced where none exists today.
- `board.html`'s single `htmx:afterSwap` listener (currently only doing
  `showModal()` on `#detail` and the revision-dedup IIFE) is confirmed to be
  the only swap-lifecycle hook in the file, and is confirmed to fire for all
  three dynamic paths: `#board`'s `hx-trigger="sse:board"` refresh, `#detail`'s
  `hx-get="/fragment/task/{id}"`, and the restart button's
  `hx-post=".../restart"` (all three use `hx-swap="innerHTML"`, all trigger
  `htmx:afterSwap` on `document.body` per htmx's event bubbling). Reusing it
  rather than adding a second listener is correct — one chokepoint, one place
  that can regress.
- **Invariant #5** (`api/` and `projection.py` must not know what the harness
  runs on) is satisfied by construction: the design adds no Python-side
  knowledge of timezones anywhere. `test_architecture.py::test_api_does_not_
  import_drivers` only walks `.py` files under `api/`, so it cannot see this
  change at all — correctly, since nothing in `api/*.py` changes.
- No port, no `BoardView`/`ArtifactView` contract, no JSON shape changes. This
  keeps the change entirely inside the "edges" layer per the module map —
  template + static asset, not orchestration.

## Confirmed compatibility with the existing test suite

This is the one point worth stating explicitly because it's easy to get
wrong: `tests/test_api_html.py` asserts the **raw UTC string** appears in
server-rendered HTML bodies, e.g.

```python
assert "2026-07-19T10:00:05Z" in card_one          # test_card_shows_time_in_state_only_when_history_exists
assert "2026-07-19T10:00:05Z" in body               # test_fragment_task_shows_metadata_and_history
```

These tests use `TestClient` against server-rendered HTML — no JS executes.
The design's choice to keep the raw UTC string as both the `<time>` element's
text content *and* its `title` (client-side JS only overwrites `textContent`
after the page loads, which `TestClient` never triggers) means these
assertions keep passing unmodified. Do not "clean up" the server-rendered
fallback text to something shorter or reformatted — that would silently break
this existing coverage and remove the no-JS/scraper fallback FR-2 relies on.
This constrains the template edit precisely: the `<time>` element's inner text
must be the exact same string the raw interpolation produces today.

## Proposed architecture (component boundaries, confirmed)

```
Server (Jinja, unchanged data)          Client (new, browser-only)
──────────────────────────────          ──────────────────────────
_columns.html, _task.html               format-local-time.js
  emit <time datetime="UTC"               window.localizeTimes(root?)
       title="UTC">UTC</time>              — Intl.DateTimeFormat, per node
  (raw string unchanged, 3 call sites)     — try/catch → leave fallback text

board.html (wiring only)
  <script src="/static/format-local-time.js">
  localizeTimes()                    // whole-page pass, initial load
  htmx:afterSwap → localizeTimes(event.detail.target)  // every subsequent swap
```

This is the right shape and the right layer for the conversion: the browser
is the only actor in this system that can know the viewer's timezone
(`Intl` reads it from the OS), and it's also the only layer where doing so
carries zero blast radius — nothing downstream of the DOM observes the
converted string. No alternative (server-side timezone detection via
`Accept-Language`/headers, a user-settable preference, a new `?tz=` query
param) is justified: the acceptance criteria explicitly ask for browser-
system-derived local time, not a user override, and a server-side approach
would require a new port surface for something the backend has no business
knowing (violates invariant #5's spirit even though #5 only names `api/`/
`projection.py` literally).

## Implementation guidance

1. **New file**: `src/harness/api/static/format-local-time.js`. Exactly the
   shape in `design-01.md` §1 — a single `window.localizeTimes(root)` global,
   `querySelectorAll('time[datetime]')`, `Intl.DateTimeFormat(undefined, {
   dateStyle: 'medium', timeStyle: 'medium', timeZoneName: 'short' })`, wrapped
   per-node in try/catch. `undefined` locale is deliberate — it defers to the
   browser's locale, not a hardcoded one.
2. **Template edits** — three call sites, each a mechanical wrap of an
   existing bare `{{ }}` interpolation in a `<time>` element, text content
   byte-identical to today's output:
   - `_columns.html:16` — `task.history[-1].at`
   - `_task.html:14` — `task.created`
   - `_task.html:23` — `entry.at` (inside the history `{% for %}` loop)
   No other row in `_task.html`'s table changes — `status`, `lastOutcome`,
   `lockId`, `outcome`, `actor` are not timestamps and must stay bare text.
3. **`board.html` wiring**, in this order relative to existing script tags:
   - Add `<script src="/static/format-local-time.js"></script>` after
     `sse.js` and before the existing inline `<script>` block (ordering
     matters — the inline block must be able to call `localizeTimes`
     immediately).
   - At the top of the inline block (so it runs on first parse, before any
     swap can occur), call `localizeTimes()` unscoped once, for the
     server-rendered `{% include "_columns.html" %}` content.
   - Inside the existing `htmx:afterSwap` listener
     (`document.body.addEventListener('htmx:afterSwap', ...)`), add
     `localizeTimes(event.detail.target)` as the **first statement**, ahead of
     the existing `showModal()` check — it must run unconditionally for every
     swap target (`#board` and `#detail` both), not just when
     `target.id === 'detail'`.
4. **Test addition (required, not in the design doc as written)**: the design
   and plan cover manual verification only. Given `tests/test_api_html.py`
   already exercises every affected template path with `TestClient`, add
   assertions there — no new test file needed:
   - `_columns.html`/`_task.html` output now contains
     `<time datetime="2026-07-19T10:00:05Z"` (and the matching `title=`) for
     each of the three call sites, in addition to the existing raw-string
     assertions (which keep passing as-is, per above).
   - This is server-side-only coverage (`TestClient` doesn't execute JS) —
     it verifies the markup contract `format-local-time.js` depends on, not
     the conversion itself. That's an acceptable and correct boundary: it's
     the same reasoning that keeps `api/` ignorant of timezones in the first
     place. Client-side conversion correctness (FR-1, FR-2, FR-4) is verified
     manually per the plan's step 4, since this repo has no JS test runner —
     introducing one is out of scope for a three-file, no-dependency change.

## Risks and mitigations

- **Risk**: forgetting to call `localizeTimes` unconditionally in
  `htmx:afterSwap` (e.g. leaving it nested inside the `if (target.id ===
  'detail')` branch) silently leaves the board's own cards in UTC after every
  SSE refresh, while the detail dialog looks correct — an easy-to-miss partial
  regression since the initial page load would still look right.
  **Mitigation**: the test addition above (assert `<time datetime=` markup
  survives in `/fragment/board` output) doesn't catch this JS-only bug, so
  call it out here as a manual-verification checkpoint — plan step 4 already
  lists "live SSE board update" as a case to check; do not skip it.
- **Risk**: `new Date(node.getAttribute('datetime'))` on the exact
  `%Y-%m-%dT%H:%M:%SZ` format the harness emits is safe (ISO-8601 UTC, uniform
  cross-browser per spec), but any future change to the timestamp format
  (e.g. adding microseconds, or dropping the trailing `Z`) is silent breakage
  for this script with no compile-time signal. **Mitigation**: none needed
  now — the try/catch degrades to raw-text fallback rather than a blank/broken
  page, which is exactly the NFR's required behavior. Worth a one-line comment
  in `format-local-time.js` noting the expected input shape, so a future
  format change is at least locally documented.
- **Risk**: `timeZoneName: 'short'` renders as a numeric offset (e.g. `GMT+2`)
  rather than an abbreviation (`CEST`) in some browser/locale/zone
  combinations — this is a known `Intl` platform quirk, not a bug in this
  design. **Mitigation**: none required — FR-2 only asks the zone be visible
  and unambiguous, and `GMT+2` satisfies that as much as `CEST` does. Not
  worth chasing browser-specific formatting parity.

## Prerequisites before implementation begins

None. No new dependency, no port, no schema, no migration. The one open
question in `plan-01.md` (exact display format) is already resolved with a
stated, reasonable default (`dateStyle: 'medium', timeStyle: 'medium',
timeZoneName: 'short'`) — proceed with it; it is not a blocking ambiguity.

## Summary for implementers

Build exactly what `design-01.md` specifies: one new static JS file, three
template call sites wrapped in `<time>`, two edits to `board.html`'s existing
inline script. Add the markup-contract assertions to
`tests/test_api_html.py` described above. Do not touch `routes.py`,
`app.py`, `projection.py`, any port, or the on-disk/JSON timestamp format —
none of that is in scope, and the design correctly leaves all of it alone.
