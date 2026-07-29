# Merge conflict resolution — PR #142

Merged `origin/main` into this branch (the board filter-bar feature). Two files
had conflicts; both were structural collisions between this branch's older,
flat `_columns.html` markup and `main`'s concurrent refactor into a
`board_column` macro with `inbox`/`step`/`terminal` grouped sections.

## `src/harness/api/templates/_columns.html`

This branch had added `data-repository`/`data-search` attributes (for the
client-side filter bar) to the old flat, per-column card markup. `main` had
in the meantime replaced that flat markup with a shared `board_column` macro
and split the board into `Waiting`/`<workflow>` workflow/`Finished` sections.

Resolution: kept `main`'s macro-based structure (already present unconflicted
throughout the rest of the file) and folded this branch's `data-repository`
and `data-search` attributes into the macro's single `card` div, so every
card — regardless of which section renders it — carries the attributes the
filter bar's `applyFilters()` (in `board.html`) reads. The leftover raw
flat-markup fragment from this branch's side of the conflict (superseded by
the macro) was dropped entirely.

## `tests/test_api_html.py`

Both branches added independent new tests after
`test_card_shows_last_outcome`: this branch added
`test_index_renders_filter_bar_with_repository_options` and
`test_card_carries_data_repository_and_data_search_attributes`; `main` added
the outcome-badge/accent-stripe tests (`test_outcome_badge_names_the_step_...`,
`test_green_accent_is_only_for_a_task_that_actually_finished`, etc.). No
logic overlap — resolved by keeping both blocks, this branch's tests first,
followed by `main`'s. The shared `client` fixture already threads
`repository_names=("app-backend", "other-repo")` into `Board`, so both test
groups run against the same fixture unchanged.

## Verification

- No conflict markers remain anywhere in the tree (checked via grep).
- `pytest -q tests/test_api_html.py tests/test_architecture.py`: 74 passed.
- Full suite `pytest -q`: 1679 passed, 1 skipped.
