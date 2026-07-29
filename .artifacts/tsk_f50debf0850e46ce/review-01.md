# Review — Artifacts tab move

## Diff reviewed
`c6ec5fa` — `src/harness/api/templates/_task.html`, `src/harness/api/static/app.css`, `tests/test_api_artifacts.py`.

## Checked against acceptance criteria

- Tab strip gains a fourth `data-tab="artifacts"` button (`_task.html:22`), alongside Info/History/Output, using the same generic tab-switch mechanism already driving the other three (no new JS, none added).
- New `data-panel="artifacts"` section (`_task.html:70-87`) holds the step/attempt/name table with links to `/api/tasks/{id}/artifacts/{step}/{attempt}/{name}` — identical link target as before, just relocated.
- The old trailing `<h3>artifacts</h3>` block under the Info panel's `kv` list is gone; Info panel now ends at the `data` row (`_task.html:41-43`).
- Empty state preserved: `{% else %}<p class="hint">No artifacts yet.</p>{% endif %}` inside the artifacts panel.
- `.table-scroll` wrapper reused verbatim around the artifacts table, so a long attempt list scrolls within the panel rather than blowing out the dialog.
- Nice-to-have count badge implemented: `<span class="tab__count">{{ artifacts|length }}</span>` shown only when `artifacts` is non-empty, styled in `app.css` (`.tab__count`, plus an `.active` variant) modeled on the existing `.column__count` pattern.
- No changes to `ArtifactView`, `routes.py`'s artifact handlers, or the artifact URL scheme — confirmed via `git show` and `grep`; `routes.py` still only calls `artifacts.list(task_id)` / `artifacts.read(...)`.
- Architecture invariants intact: `api/` imports no driver, touches only `ArtifactView` for artifacts (`test_architecture.py` passes).

## Tests

Two new tests in `tests/test_api_artifacts.py`:
- `test_fragment_task_has_artifacts_tab_with_count_and_table_in_its_own_panel` — asserts the tab button carries the count badge, the artifacts panel exists, artifact content (`plan.md`) is present in the artifacts panel and absent from the info panel, and the old `<h3>artifacts</h3>` heading is gone.
- `test_fragment_task_shows_no_artifacts_hint_and_no_badge_when_empty` — asserts no count badge when there are no artifacts and the "No artifacts yet." hint renders in the artifacts panel.

## Verification run this turn

- `.venv/bin/pytest -q tests/test_api_artifacts.py tests/test_architecture.py` → 36 passed.
- `.venv/bin/pytest -q` (full suite) → 1511 passed, 1 skipped.

## Verdict

Implementation matches design-01.md/architecture-01.md exactly, meets every acceptance criterion (including the nice-to-have count badge), adds direct test coverage for the new behavior, and introduces no invariant violations. No issues found.
