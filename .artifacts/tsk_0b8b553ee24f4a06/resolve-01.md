# Merge conflict resolution — PR #111

## Conflict

Merging `origin/main` into this branch produced a single conflict, in
`tests/test_api_html.py`, at the boundary between two independently added
tests right after `test_fragment_task_shows_metadata_and_history`:

- **HEAD** (this branch, the workflow-defined-outcomes work plus the earlier
  client-side timezone localization) added
  `test_fragment_task_times_are_time_elements_for_client_side_localization`,
  asserting the task fragment's "created" field and history "time" column
  render as `<time datetime=... title="... UTC">` elements.
- **origin/main** added `test_fragment_task_shows_copy_id_button`, asserting
  the task fragment renders a `copy-id-btn` element with `data-copy-id`.

The two tests are unrelated (different features, added at the same source
line) — no actual logic conflict, just adjacent insertions.

## Resolution

Kept both tests, one after the other (HEAD's first, then origin/main's),
removing all conflict markers. No other file in the merge had conflicts —
every other file auto-merged cleanly.

## Verification

- `grep` across `*.py`/`*.html`/`*.js`/`*.css` confirms no leftover
  `<<<<<<<`/`=======`/`>>>>>>>` markers outside of legitimate fixture/doc
  content (a git-workspace test fixture and a `cli.py` docstring that
  literally describe conflict markers, and an unrelated `=====` separator
  comment in `sse.js`).
- `git status` shows no unmerged paths remaining.
- Full test suite: `.venv/bin/pytest -q` → **1267 passed, 1 skipped** (the
  skip is the opt-in real-`claude` smoke test, expected to skip without
  `HARNESS_SMOKE_CLAUDE=1`).
