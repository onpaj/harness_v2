# resolve-01: merge conflict resolution for PR #112

## Conflict

Merging `origin/main` into this branch conflicted in three files. This
branch's own change (from the earlier `copy-id` task) added a copy-to-clipboard
button next to the task id; `origin/main` had, in the meantime, redesigned the
task-detail panel (tabs, header with Restart/Delete/Close, `.kv` list,
artifacts table) and moved the board's page CSS out of an inline `<style>`
block into `src/harness/api/static/app.css`. The two changes touched the same
regions, so both needed reconciling rather than a straight pick of one side.

## Resolution

- **`src/harness/api/templates/_task.html`** — kept `origin/main`'s
  `task-detail` structure (header/tabs/kv/artifacts) and added the copy-id
  `<button>` as a sibling of the `<h2>` inside `.task-detail__header`, next to
  the existing Restart/Delete/Close buttons. `data-copy-id` still copies
  `task.id` even though the header now displays `task.data.title or task.id`.
- **`src/harness/api/templates/board.html`** — dropped this branch's inline
  `<style>` block entirely (superseded by `app.css`, per `origin/main`); the
  `.copy-id-btn` click handler further down the file was untouched by the
  conflict and needed no changes.
- **`src/harness/api/static/app.css`** — added `.copy-id-btn`/`.copy-id-btn.copied`
  rules next to `.task-detail__close`, reusing the shared theme tokens
  (`--surface-2`, `--radius-sm`, `--done-bg`/`--done-fg`) instead of the old
  branch's hardcoded light/dark hex colors, so the button follows the app's
  existing light/dark theming automatically.
- **`tests/test_api_html.py`** — kept both this branch's
  `test_fragment_task_shows_copy_id_button` and `origin/main`'s two
  workflow/step-dash regression tests; no test content needed changing, only
  the ordering of the three functions.

## Verification

`pytest -q` — full suite passes: **1222 passed, 1 skipped**. No markers
(`<<<<<<<`/`=======`/`>>>>>>>`) remain in any file.

```json
{"outcome": "done", "summary": "Resolved conflicts in _task.html, board.html and test_api_html.py by combining origin/main's redesigned task-detail panel with the copy-id button feature (added CSS to app.css instead of the removed inline style block); full suite passes (1222 passed, 1 skipped)."}
```
