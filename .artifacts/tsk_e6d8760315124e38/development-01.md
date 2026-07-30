# Development — repository select filter + fulltext filter on the board UI

Implements `plan-01.md` / `design-01.md` as approved in `architecture-01.md`,
including its two implementer-facing notes (explicit `select` CSS since none
existed to reuse; null-safe Jinja for `data-search`).

## What changed

- **`src/harness/ports/board.py`** — `Board` gained `repository_names: tuple[str,
  ...] = ()` (sorted, defaults to `()` so every existing construction site
  keeps compiling), included in `to_dict()`.
- **`src/harness/projection.py`** — `BoardProjection.__init__` gained an
  optional `repository_names: Sequence[str] = ()` parameter, stored sorted
  once at construction (mirrors how `steps`/`workflows` are handled); copied
  onto the `Board` built by `snapshot()`.
- **`src/harness/app.py`** — `build()`'s existing `BoardProjection(...)` call
  now threads `repository_registry.names() if repository_registry is not None
  else ()` through — the same lenient fallback `known_repositories` already
  uses a few lines down for `VerifyBehavior`. No new parameter, no new port
  method, no new import.
- **`src/harness/api/templates/board.html`** — a new `.filter-bar` div (a
  repository `<select id="filter-repo">` populated from
  `board.repository_names` plus an "All repositories" default, and a
  `<input type="search" id="filter-text">`) sits as a sibling *before*
  `#board`, so it survives the SSE `hx-swap="innerHTML"` untouched. New script:
  `applyFilters(boardEl)` toggles `.hidden-by-filter` on every `.card` whose
  `data-repository`/`data-search` don't match the current control values, then
  recomputes each `.column`'s visible count and toggles `.column--empty` for
  dimming. Called on load, on the select's `change` and the input's `input`
  events, and now also from the existing `htmx:afterSwap` handler right after
  `applyActiveTab(...)` — mirroring that function's three call sites exactly.
- **`src/harness/api/templates/_columns.html`** — each `.card` gains
  `data-repository="{{ task.repository | basename }}"` (exact-match target for
  the select) and a `data-search="..."` blob (`data.title` or the id, the id,
  repository basename, worktree basename, last outcome — all `| lower`),
  computed once server-side.
- **`src/harness/api/static/app.css`** — `.filter-bar` layout rule; explicit
  sizing for `.filter-bar select` (no existing bare `select` rule to reuse,
  confirmed in review); `input[type="search"]` folded into the existing
  `input[type="text"], textarea` sizing/focus rules; `.card.hidden-by-filter {
  display: none; }`.

## Deviation from the design (bug caught by the existing test suite)

The design's pseudocode used the *raw* `task.repository`/`task.worktree`
values (not basename-filtered) for both `data-repository` and the
`repository`/`worktree` portion of `data-search`. Running the full suite
caught `test_card_shows_repo_and_worktree_basename_not_path`, which asserts
the rendered HTML never leaks a task's full local filesystem path (only the
basename the card visibly shows). Fixed by routing both new attributes
through the existing `| basename` Jinja filter — consistent with what
`.card__repo` already displays, and with invariant #15 (`task.repository` is
a name in production; the test's path-shaped fixture value is exactly the
case this filter exists for).

## Why this holds the invariants

- **Invariant #5/#33** — `api/routes.py` is unchanged; `projection.py` gained
  a plain `Sequence[str]` constructor parameter, no new import.
  `test_architecture.py` (driver-import checks) passes.
- **Invariant #8** — `task.repository` is read only inside Jinja (an
  attribute) and the plain-data `Board.repository_names` field; never touched
  by `router.py`/`dispatcher.py`/`consumer.py`.
- View-only feature — no new HTTP surface, no query params, no routing change.

## Tests added

- `tests/test_board_port.py::test_board_repository_names_default_and_to_dict`
- `tests/test_projection.py::test_snapshot_repository_names_defaults_empty`,
  `::test_snapshot_repository_names_is_sorted`
- `tests/test_app.py::test_build_threads_repository_registry_names_into_the_board`,
  `::test_build_without_repository_registry_leaves_board_repository_names_empty`
- `tests/test_api_html.py::test_index_renders_filter_bar_with_repository_options`,
  `::test_card_carries_data_repository_and_data_search_attributes` (plus the
  fixture's `Board` now carries `repository_names=("app-backend",
  "other-repo")`, which also exercises the existing
  `test_card_shows_repo_and_worktree_basename_not_path` against the new
  attributes)

The interactive hide/show/count/AND-compose/SSE-survival behavior has no JS
test harness in this repo (confirmed: no playwright/selenium/puppeteer
anywhere in `tests/`) — verified instead by tracing the exact `applyFilters`
algorithm against representative rendered card data with `node -e` (repo-only,
text-only, both combined, both cleared — all four produced the expected
hidden/visible sets) and by rendering the real template through a `TestClient`
to confirm the filter bar and per-card attributes come out as designed.

## How to verify

```sh
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Result: 1516 passed, 1 skipped (the opt-in `HARNESS_SMOKE_CLAUDE=1` test),
0 failed.

For a manual/interactive check: `harness serve` (or `run` skill) against a
`repos.json` with 2+ repositories, open `/`, confirm the filter bar renders
with all registered repos plus "All repositories", pick one and confirm only
matching cards + updated counts remain, type into the text box, clear both,
and force an SSE refresh (e.g. submit a task) mid-filter to confirm the
`<select>`/`<input>` values and their filtering effect both survive the swap.
