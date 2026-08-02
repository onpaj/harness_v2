# Development — Artifacts tab in task detail dialog

Implemented exactly as specified in `design-01.md` / `architecture-01.md`: a
template/CSS-only rearrangement, no port, route, or driver change.

## Files changed

- `src/harness/api/templates/_task.html`
  - Added a fourth tab button to `<nav class="tabs">`:
    `<button class="tab" data-tab="artifacts">Artifacts{% if artifacts %} <span class="tab__count">{{ artifacts|length }}</span>{% endif %}</button>`
    — no badge when the task has no artifacts, `Artifacts N` when it does.
  - Removed the `<h3>artifacts</h3>` heading and the artifact table/hint block
    from the end of the `info` panel (`data-panel="info"`); that panel now
    ends with the `kv` list (last row still `data`).
  - Added a new `<section class="tab-panel" data-panel="artifacts">` after the
    `output` panel, holding the artifact table/hint block moved verbatim (same
    `.table-scroll` wrapper, same table markup, same link target
    `/api/tasks/{id}/artifacts/{step}/{attempt}/{name}`, same "No artifacts
    yet." empty state). No new `<h3>` inside — consistent with `history`/
    `output` having no in-panel heading duplicating their own tab name.
  - Tab switching needed no script change: `board.html`'s delegated click
    listener toggles `.active` by matching `data-tab`/`data-panel` and is
    already generic over the number of tabs.

- `src/harness/api/static/app.css`
  - Added `.tab__count` (small pill badge, sized to sit inside a tab button)
    and `.tabs .tab.active .tab__count` (swaps to on-accent colors so the
    badge stays legible against the active tab's accent background), placed
    right after the existing `.tabs .tab.active` rule near line 306, modeled
    on `.column__count`.

- `tests/test_api_artifacts.py`
  - Added `test_fragment_task_has_artifacts_tab_with_count_and_table_in_its_own_panel`:
    asserts the tab button carries the `Artifacts <span class="tab__count">2</span>`
    badge, the `data-panel="artifacts"` section exists, the artifact table
    content (`plan.md`) is present in the artifacts panel and absent from the
    info panel, and the old `<h3>artifacts</h3>` heading is gone entirely.
  - Added `test_fragment_task_shows_no_artifacts_hint_and_no_badge_when_empty`:
    asserts a task with no artifacts renders a plain `Artifacts` tab (no
    badge) and the "No artifacts yet." hint inside the artifacts panel.
  - Left the pre-existing `test_fragment_task_lists_artifacts_with_links` and
    `test_api_imports_only_the_artifact_port_not_drivers` untouched — both
    still pass, confirming the substring/import-boundary contracts are
    unaffected.

## Verification

```
grep -c '<h3>artifacts</h3>' src/harness/api/templates/_task.html   # 0
grep -c 'data-panel="artifacts"' src/harness/api/templates/_task.html  # 1
.venv/bin/pytest -q
```

Ran the full suite in a freshly created `.venv` (`python3.11 -m venv .venv &&
.venv/bin/pip install -e ".[dev]"`, none existed in this worktree yet):
**1511 passed, 1 skipped** (the skip is pre-existing/unrelated — the opt-in
`HARNESS_SMOKE_CLAUDE` smoke test). `tests/test_architecture.py` (invariants
#5/#33/#11 guard) and `tests/test_api_artifacts.py` both pass in full.

No manual browser check was performed (no running dev server in this
non-interactive step); the template/CSS change was verified by grep and by
the new fragment-rendering tests above, which exercise the exact HTML the
browser would receive.

## Acceptance criteria

- [x] Fourth tab `Artifacts` added to the tab strip, switches via the
      existing `data-tab`/`data-panel` mechanism — no new script.
- [x] Artifacts table (step/attempt/name, name linking to the artifact URL)
      renders in the new `artifacts` panel; removed from `info`.
- [x] Empty state still shows "No artifacts yet." inside the artifacts panel.
- [x] `.table-scroll` wrapper preserved, so a long attempt list scrolls
      within the panel.
- [x] Tab carries an `Artifacts N` count badge (`.tab__count`) when non-empty;
      no badge when empty.
- [x] No `ArtifactView`/route/URL-scheme change.
- [x] `api/` still imports no driver and touches only `ArtifactView` for
      artifacts — verified by the existing `test_architecture.py` and
      `test_api_imports_only_the_artifact_port_not_drivers` tests, both
      passing unchanged.
