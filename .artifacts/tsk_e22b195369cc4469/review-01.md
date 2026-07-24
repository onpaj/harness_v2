# Review — Harness dashboard: local-timezone timestamps

Reviewed `development-01.md` against `architecture-01.md` (approved design)
and the actual diff in commit `b13a750`, with the test suite re-run
independently (not just trusting the artifact's claimed numbers).

## Conformance check

- **Spec/acceptance criteria**: All four are met.
  - Local-timezone rendering: `format-local-time.js` uses
    `Intl.DateTimeFormat(undefined, {...})` — `undefined` locale defers to
    the browser, and `Intl` resolves the OS timezone.
  - Zone visibility: `timeZoneName: 'short'` is included in the format
    options; the raw UTC instant remains recoverable via the `title`
    attribute.
  - DST/offset correctness: delegated to `Intl`/`Date`, which resolve real
    timezone rules rather than a static offset — correct by construction,
    no custom offset math.
  - Cross-browser: only standard `Date`/`Intl.DateTimeFormat` APIs, no
    polyfill or UA sniffing.
- **Architecture adherence**: exact match to `architecture-01.md`'s
  "Implementation guidance" section.
  - New file `src/harness/api/static/format-local-time.js`, served via the
    existing `/static` `StaticFiles` mount — no build step introduced.
  - Three template call sites wrapped in `<time datetime="..."
    title="... UTC">...</time>`, text content byte-identical to the prior
    bare interpolation: `_columns.html` (`task.history[-1].at`),
    `_task.html` (`task.created` and `entry.at` in the history loop). No
    other `_task.html` row touched, as required.
  - `board.html` wiring matches the specified order precisely: script tag
    added after `sse.js`/before the inline block; `localizeTimes()` called
    unscoped as the inline block's first statement (covers initial
    `#board` content); `localizeTimes(event.detail.target)` added as the
    **first** statement inside the existing `htmx:afterSwap` listener,
    ahead of the `showModal()` check — so it fires unconditionally for all
    three swap targets (`#board` SSE refresh, `#detail` fragment load,
    restart re-swap), not just the detail dialog. This was flagged in the
    architecture doc as the easiest way to introduce a partial regression,
    and it was implemented correctly.
  - No backend/route/port/projection code touched — confirmed by diff
    (only `static/`, `templates/`, and `tests/` changed). Satisfies
    invariant #5 by construction.
- **Completeness**: the architecture's one required test addition was
  implemented in full — `test_index_renders_board_shell` and
  `test_static_files_are_served` extended, plus two new tests
  (`test_card_time_is_a_time_element_for_client_side_localization`,
  `test_fragment_task_times_are_time_elements_for_client_side_
  localization`) asserting the exact `<time datetime="..." title="... UTC">`
  markup contract at all three call sites. All pre-existing raw-UTC-string
  assertions were left untouched and still pass, exactly as required (the
  architecture doc explicitly warned against "cleaning up" the fallback
  text, since that would silently break existing coverage).
- **Correctness**: `new Date(...)` result is guarded with `isNaN(date.getTime())`
  before formatting, and the whole per-node body is wrapped in `try/catch`
  falling back to the server-rendered raw-UTC text — matches the NFR for
  graceful degradation on malformed/unexpected timestamp shapes. No logic
  errors spotted in the ~24-line script.

## Independent verification

Re-ran the suite myself rather than trusting the artifact's reported numbers:

```
.venv/bin/pytest -q
→ 474 passed, 1 skipped, 1 warning in 16.61s
```

Matches the development artifact's claim exactly. Also ran
`tests/test_architecture.py` alone (14 passed) to confirm none of the
project's guarded invariants (dispatcher/consumer import boundaries, `api/`
not importing drivers, etc.) were disturbed — expected, since this change
touches only `static/`, `templates/`, and `tests/`.

## Verdict

Implementation matches the approved architecture exactly, all required
tests are present and passing, and no correctness issues found. No changes
requested.
