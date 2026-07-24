# Merge conflict resolution — PR #111

Merged `origin/main` into this branch, resolving conflicts in 4 files
(`src/harness/api/templates/_columns.html`, `_task.html`, `board.html`,
`tests/test_api_html.py`).

## Context

This branch adds client-side local-timezone timestamp display: `<time datetime
title>` markup + `static/format-local-time.js` (`Intl.DateTimeFormat`),
wired into `board.html`'s `htmx:afterSwap` hook.

`origin/main` had meanwhile landed a larger board/detail redesign: multi-workflow
tab strip, tabbed task-detail (info/history/output), a delete button, and a
server-side `shorttime` Jinja filter (compact UTC display, e.g. "Jul 19, 10:00").

## Resolution

Kept `origin/main`'s structure (tab strip, workflow panels, `card__*`/`is-*`
classes, tabbed detail view) as the base, and layered this branch's
client-side localization on top of it: every timestamp is now a
`<time datetime="..." title="... UTC">` element whose text content starts as
the `shorttime`-filtered compact UTC string and is replaced client-side by
`localizeTimes()` once the page/fragment loads. Dropped the old inline
`<style>` block in `board.html` (superseded by `app.css`, which already
carries the new class names); kept the `format-local-time.js` script tag and
wired `localizeTimes()` to run on initial load and unconditionally inside the
`htmx:afterSwap` listener, ahead of the tab/modal logic.

`tests/test_api_html.py`: kept both conflicting test functions — the
client-side-localization contract test and the FR-9 dash-fallback tests —
since they cover unrelated behavior.

`tests/test_api_html_mobile.py` was not flagged as conflicted by git (it's a
new file only on the `origin/main` side) but pinned a stale expectation
(`title="...Z"` with no `UTC` suffix) that contradicts the now-merged `<time>`
convention already pinned by `test_api_html.py`. Updated that one assertion to
`title="...Z UTC"` to match.

## Verification

Full suite: `1223 passed, 1 skipped` (the skip is the opt-in
`test_smoke_claude.py`, gated on `HARNESS_SMOKE_CLAUDE=1`).

```json
{"outcome": "done", "summary": "Resolved the 4-file merge conflict: layered client-side timezone <time> localization onto origin/main's board/detail redesign; fixed a stale title-attribute assertion in test_api_html_mobile.py. Full suite passes: 1223 passed, 1 skipped."}
```
