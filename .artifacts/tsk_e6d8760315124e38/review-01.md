# Review — repository select filter + fulltext filter on the board UI

## What I checked

- The diff (`81e447a`) against `plan-01.md`/`design-01.md`/`architecture-01.md`
  and the task's acceptance criteria, line by line.
- Full suite: `.venv/bin/pytest -q` → 1516 passed, 1 skipped, 0 failed (matches
  `verify-01.md`). `tests/test_architecture.py` alone → 27 passed (invariants
  #5/#33/#8 guards untouched).
- **The actual interactive filter behavior**, not just the code and the unit
  tests. `architecture-01.md` explicitly flagged that this repo has no JS test
  harness and told the implementation step not to let "tests pass" stand in
  for "the filter actually filters." `development-01.md` disclosed (honestly)
  that it verified only by tracing the algorithm by hand and rendering markup
  through `TestClient` — real DOM interaction was never exercised. Since that
  gap was explicitly called out as a risk to close, I closed it myself:
  rendered the real `/` and `/fragment/board` responses through the app's own
  `TestClient` (same fixture as `test_api_html.py`, two repos + three cards:
  one repo'd, one repo-less, one titled), loaded the actual HTML +
  `board.html`'s actual inline `<script>` into `jsdom`, and drove it exactly
  like a browser would: dispatched `change`/`input` events on the real
  `<select>`/`<input>` elements and read back real `classList`/`textContent`
  state. Results, all correct:
  - No filters → all 3 cards visible, column count 3.
  - `repo=app-backend` → only the matching card visible, count 1.
  - `text=FIX` (mixed case) → matches `"Fix the login bug"` case-insensitively,
    count 1.
  - Both set to a non-overlapping combination → 0 visible, count 0 (AND, not
    OR).
  - Both cleared → all 3 restored.
  - Whitespace-only text (`"   "`) → filters nothing (trimmed to empty).
  - Repo filter for a registered repo with zero matching cards
    (`other-repo`) → 0 visible, count 0 — no crash, no stale count.
  - Simulated the SSE path for real: set `repo=app-backend`, replaced
    `#board`'s `innerHTML` with the actual `/fragment/board` markup (which
    contains no filter bar), fired `htmx:afterSwap` with `detail.target` =
    the board element — the newly-swapped cards were correctly re-filtered
    (count 1) and the `<select>`'s value was untouched throughout (it lives
    outside `#board`, so the swap never touched it).

## Conformance

- **Spec/acceptance criteria** — all met: filter bar with select + fulltext
  input; select populated from `board.repository_names` (sourced from
  `RepositoryRegistry`, not an ad-hoc task scan — confirmed in `app.py`'s
  `build()` and its two new tests) plus an "All repositories" default;
  repo filter hides non-matching cards and column counts follow; fulltext is
  case-insensitive and empty-input-is-a-no-op; both compose as AND; filter
  state (the live control values) survives the SSE `#board` innerHTML swap by
  construction (the bar sits outside `#board`) and the *effect* is correctly
  reapplied from the existing `htmx:afterSwap` handler, right where
  `applyActiveTab` already does the same thing.
- **Architecture** — matches `design-01.md` exactly: `Board.repository_names`
  threaded through `BoardProjection`/`app.py::build()` mirroring the
  `FilesystemProcessAdmin.repository_names` precedent that
  `architecture-01.md` called out; filter bar as a `#board` sibling; single
  `applyFilters()` reapplied from the same three call sites design specified.
  One deliberate, well-justified deviation from the design's pseudocode: card
  attributes are basename-filtered (`| basename`) rather than using the raw
  `task.repository`/`task.worktree` strings, because the existing
  `test_card_shows_repo_and_worktree_basename_not_path` test caught the design
  pseudocode leaking a full local path — the fix is consistent with how
  `.card__repo` already renders and with invariant #15.
- **Invariants** — #5/#33 (`api/`/`projection.py` import no driver): only a
  plain `Sequence[str]` constructor parameter was added, no new import, guard
  tests pass. #8 (routing never reads `repository`): `router.py`/
  `dispatcher.py`/`consumer.py` untouched by this diff; `task.repository` is
  read only in Jinja and the pure-data `Board.repository_names` field.
- **Completeness** — tests cover the data plumbing at every layer
  (`Board.to_dict()`, `BoardProjection` sorting/default, `build()` wiring with
  and without a registry, rendered `<select>`/`data-*` output) and the
  previously-unverified interactive behavior is now actually exercised (see
  above), closing the one real gap `architecture-01.md` flagged.
- **Correctness** — no logic errors found. `_basename(None)` returns `""`,
  matching the repo-less card's `data-repository=""`, which correctly falls
  through only the "All repositories" (`value=""`) option — verified live,
  not just read.

## Verdict

No functional requirement is unmet, nothing conflicts with the approved
architecture, the required tests are present, and the one risk the
architecture step asked to be closed (interactive filter behavior) has now
been independently verified against the real rendered markup and the real
script, not just reasoned about.
