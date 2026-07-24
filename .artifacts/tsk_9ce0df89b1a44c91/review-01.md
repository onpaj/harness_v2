# Review — Mobile-first board and admin UI redesign

Reviewed `development-01.md` against `plan-01.md` / `design-01.md` /
`architecture-01.md` and the actual diff (`0c8027b..HEAD`), by reading every
changed file (not just the development report's paraphrase) and re-running
the suite.

## What I checked

- **Full suite**: `.venv/bin/pytest -q` → 489 passed, 1 skipped (the opt-in
  `HARNESS_SMOKE_CLAUDE` test) — matches the claim, no existing assertion
  weakened.
- **Scope cut re-validated independently.** `find src/harness/api -type f`
  confirms no `admin/` directory, no agents/workflows routes/templates exist
  today; `find tests -iname "*agent*" -o -iname "*workflow*"` confirms
  `tests/test_api_agents.py` / `tests/test_api_workflows.py` do not exist.
  The plan/design/architecture chain's decision to drop the admin UI from
  scope (it would require inventing ports/routes, contradicting the task's
  own "presentation only" brief) is correct and was independently
  reconfirmed, not just carried forward on trust.
- **`routes.py` diff**: exactly one addition, `_shorttime` + its filter
  registration, same defensive pattern as `_basename` (empty/`None` pass
  through, `ValueError` caught and raw value returned). No new context, no
  new import. `tests/test_shorttime_filter.py` covers `Z`-suffix,
  explicit-offset, `None`, empty string, and unparseable input.
- **`board.html`**: viewport/theme-color/apple-* meta, `app.css` link,
  `_nav.html` include, and one new delegated click listener for tab
  switching registered on `#detail` (survives `_task.html`'s wholesale
  fragment replacement, per architecture Decision 3). The three pre-existing
  listeners (dialog open-on-swap, backdrop-click-to-close,
  innerHTML-clear-on-close which tears down the Output SSE connection) and
  the revision de-dup IIFE are byte-identical to `HEAD~1` in logic.
- **`_columns.html`**: sticky/empty-state column head, status-derived stripe
  class computed inline (no `status_class` added to the Python context, per
  architecture Decision 1), pill badges. Every guard pinned by
  `test_api_html.py` is preserved verbatim: title-falls-back-to-id,
  repo/worktree basename-join only when set, working-badge iff `lock_id`,
  outcome-badge iff `last_outcome`, `card__time` iff `task.history`, and the
  literal string `"since"` is absent when there's no history (the test
  greps for that literal, not a class name) — confirmed by reading the
  rendered conditional directly.
- **`_task.html`**: Info/History/Output tab strip with `data-tab`/
  `data-panel` attributes; Info is a stacked `<ul class="kv">` with every
  optional field's `or "—"` guard intact; History/Artifacts each wrapped in
  their own `.table-scroll` container; Output panel's SSE attributes
  (`hx-ext="sse"`, `sse-connect`, `sse-close="end"`, `id="stage-output"`,
  `sse-swap="line"`, `hx-swap="beforeend scroll:bottom"`) are character-for-
  character identical to the pre-redesign template — only the inline
  `style=` became a `.terminal` class. `task.created` and each history
  `entry.at` now render as `shorttime` text with the raw ISO string in a
  `title` attribute, satisfying `test_fragment_task_shows_metadata_and_history`'s
  raw-ISO-substring check without weakening it.
- **`_nav.html`**: data-driven single-item list rendered twice (appbar +
  tabbar) from one loop; active-state script matches `location.pathname` to
  `data-section`'s owning link's `href`.
- **`app.css`**: `grep "://" ` → no matches (no external resource, dark-mode
  block present, `min-width: 768px` media query flips `flex-direction:
  column` → `row`, `env(safe-area-inset-top/bottom)` present, 44px tap
  targets on nav/buttons/tabs.
- **Architecture invariants**: `tests/test_architecture.py` (14 tests,
  including `test_api_does_not_import_drivers`) passes; no `.py` file was
  added to `api/` besides the pre-existing `routes.py`, so invariants #5/#11
  hold by construction, not just by test.
- **New tests** (`tests/test_api_html_mobile.py`,
  `tests/test_shorttime_filter.py`) assert only new markup/behavior and
  don't touch or loosen any assertion in the pre-existing test files.

## Verdict rationale

Every functional requirement in the plan (FR-1 through FR-9) is met by the
actual markup, not just claimed. The one deliberate scope reduction (no
admin agents/workflows UI) is not a missed requirement — it targets a
feature that doesn't exist in this codebase, was flagged and justified at
every prior stage, and building it here would have violated the task's own
"presentation only, no port/route changes" constraint. No architecture
conflict, no missing required test, no correctness bug found.

```json
{"outcome": "done", "summary": "Implementation matches plan/design/architecture: full suite green (489 passed, 1 skipped), every pinned test_api_html.py contract preserved verbatim (title/repo/badge/time guards, restart button, SSE hooks byte-for-byte), shorttime filter is the only routes.py change, no driver import added to api/ (test_architecture.py green), app.css has no external URL and defines the 768px stack/row switch + dark mode + safe-area insets + 44px tap targets, and the admin agents/workflows scope cut is independently reconfirmed correct (feature genuinely absent from this repo). No changes requested."}
```
