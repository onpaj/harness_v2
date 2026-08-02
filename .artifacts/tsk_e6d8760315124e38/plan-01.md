# Plan — repository select filter + fulltext filter on the board UI

## Summary

The board (`/`) currently renders every task of the active workflow tab with
no way to narrow the view. This adds a filter bar above the board with a
repository `<select>` (pre-populated from `RepositoryRegistry`, not from the
tasks on screen) and a fulltext `<input>`, both applied client-side against
the already-rendered cards so column counts stay honest and the existing SSE
fragment-swap mechanism needs no new query params.

## Context

On a harness serving several repositories, a column becomes a wall of cards
with no way to find the one task an operator cares about. Two filters —
repository and fulltext — cover the need without touching routing, queues or
task state (invariant #8: `repository` is read only by the behavior, never by
`route()`/the dispatcher — this feature only ever reads it in the read-side
view). `api/` must keep importing no driver (invariant #5/#33): the
repository name list has to reach the template through the existing
`BoardView`/`Board` snapshot, the same read-side port surface `BoardColumn`
already carries pure view data on — not by giving `api/` a
`RepositoryRegistry`/`FilesystemRepositoryRegistry` import.

There is already a precedent for exactly this plumbing shape:
`ProcessAdmin.repository_names()` (`ports/process_admin.py`,
`drivers/fs_processes.py::FilesystemProcessAdmin`) surfaces
`RepositoryRegistry.names()` through an admin port so the process form's
`<select>` never needs a driver import. This task does the read-side
equivalent for `BoardView`.

## Functional requirements

**FR-1 — `Board` carries the available repository names as view data.**
- `ports/board.py`: add `repository_names: tuple[str, ...] = ()` to the
  `Board` dataclass (sorted tuple of names), included in `to_dict()`.
- `projection.py`: `BoardProjection.__init__` accepts an optional
  `repository_names: Sequence[str] = ()` and stores it sorted; `snapshot()`
  passes it straight onto the `Board` it builds. `BoardProjection` still knows
  nothing about `RepositoryRegistry` — it only holds the tuple of strings it
  was constructed with (mirrors how it already holds `steps`/`workflows` as
  plain data, not live ports).
- **Acceptance:** `BoardProjection(steps=(), workflows=(), repository_names=["b", "a"]).snapshot().repository_names == ("a", "b")`; default (no arg) is `()`.

**FR-2 — wiring passes the registry's names through, once, at `build()` time.**
- `app.py::build()` already receives an optional `repository_registry:
  RepositoryRegistry | None` (used today for `VerifyBehavior` and the
  `known_repos` set at line ~701). Pass
  `repository_registry.names() if repository_registry is not None else ()`
  into the `BoardProjection(...)` construction (~line 428).
- No new parameter needed on `build()` — this reuses the existing
  `repository_registry` argument `cli.py` already supplies for `harness run`
  (`cli.py:1958`) and every test that wires one for `VerifyBehavior`.
- **Acceptance:** a `build()` call with a `repository_registry` whose
  `names()` returns `["harness_v2", "other"]` yields a snapshot whose
  `repository_names == ("harness_v2", "other")`; a `build()` call with no
  registry yields `()` — matching the existing `known_repos=None` leniency
  elsewhere in `app.py`.

**FR-3 — the board page renders a filter bar.**
- `templates/board.html`: add a `.filter-bar` between `.page-header` and
  `<div id="board">` — **outside** the `hx-swap="innerHTML"` target, so the
  operator's typed/selected values are never wiped by an SSE-triggered
  `/fragment/board` swap (the input's live DOM value needs no `data-`
  round-trip, unlike the active-tab state which *does* live inside the
  swapped fragment).
  - `<select id="filter-repo">`: `<option value="">All repositories</option>`
    followed by one `<option>` per `board.repository_names`.
  - `<input id="filter-text" type="search" placeholder="Filter…">`.
- `_columns.html` is unchanged structurally, but each `.card` gains two data
  attributes the filter script reads:
  - `data-repository="{{ task.repository or '' }}"`
  - `data-search="{{ (task.data.title or task.id) ~ ' ' ~ task.id ~ ' ' ~ (task.repository or '') ~ ' ' ~ (task.worktree or '') ~ ' ' ~ (task.last_outcome or '') }}"`
    lower-cased in the template (Jinja `| lower`) so the JS does a plain
    substring match with no per-keystroke case juggling.
- **Acceptance:** viewing page source of `/` shows the `<select>` populated
  with the registry's names and an "All repositories" option; each `.card`
  carries both data attributes.

**FR-4 — filtering logic (client-side, in `board.html`'s existing `<script>`
block, alongside `applyActiveTab`).**
- `applyFilters(boardEl)`:
  1. Reads `filter-repo`'s value and `filter-text`'s value (trimmed,
     lower-cased) from the two now-persistent inputs (module-level, not
     re-queried from inside `#board`).
  2. For every `.card` inside the *currently visible* `.workflow-panel`(s) —
     actually every `.card` on the whole board, since a hidden panel's counts
     must also stay correct for when the operator switches tabs — checks:
     `repoOk = !repo || card.dataset.repository === repo` and
     `textOk = !text || card.dataset.search.indexOf(text) !== -1`. Toggles a
     `hidden-by-filter` class (not `display:none` directly in JS — a CSS rule
     does the hiding, matching the existing `.is-failed`/`.is-working` class
     idiom) based on `repoOk && textOk`.
  3. For every `.column`, recomputes the visible count (`.card` minus
     `.hidden-by-filter`) and writes it into `.column__count`; toggles
     `column--empty`/shows the `.column__empty` "No tasks" placeholder when
     the *visible* count is 0 (even if the column has cards, just all
     filtered out) — reusing the existing empty-column markup/CSS rather than
     inventing a second "no matches" state.
- Wired to run: on `input`/`change` of both filter controls, once at the
  bottom of the script (initial state — matches the existing
  `applyActiveTab(document.getElementById('board'))` call), and from inside
  the existing `htmx:afterSwap` listener right after `applyActiveTab(...)`
  (this is the "reapply after SSE swap" requirement — freshly rendered cards
  from `/fragment/board` start with no `hidden-by-filter` class, so filtering
  must be recomputed against the new DOM exactly like the active tab is
  reapplied).
- **Acceptance:** typing into the fulltext input hides non-matching cards
  immediately; selecting a repository hides cards of every other repository;
  clearing both shows everything again; after a simulated SSE swap (calling
  the same `afterSwap` handler / re-rendering `_columns.html`) the same
  filter values are still in effect without the operator retyping anything.

**FR-5 — CSS for the filter bar and hidden state.**
- `app.css`: `.filter-bar` (flex row, gap, matching `.page-header` spacing —
  reuse existing tokens, no new custom properties), `#filter-repo`/`#filter-text`
  sized like other form controls already in the sheet (`admin/agent_form.html`
  etc. — reuse those rules rather than inventing new ones), and
  `.card.hidden-by-filter { display: none; }`.
- **Acceptance:** filter bar renders inline, reasonably matches the visual
  weight of `.page-header`; no layout shift when toggling.

## Non-functional requirements

- **No new HTTP round-trip.** Filtering must not add a query param to
  `/fragment/board` or change what the SSE stream sends — the acceptance
  criteria explicitly ask for a `hx-get`-swap-safe client-side approach, and
  the existing coalesced revision-only SSE frames stay untouched.
- **No routing/state change.** Nothing under `dispatcher.py`/`consumer.py`/
  `router.py` is touched; `repository` is read only inside the Jinja template
  and the projection's plain data field (never compared against for
  placement) — invariant #8 holds unchanged.
- **`api/` imports no driver** (invariant #5/#33) — `routes.py` needs zero
  changes for FR-1–FR-5; `repository_names` arrives already resolved on the
  `Board` snapshot `index()`/`fragment_board()` already pass into the
  template.

## Data model

- `Board.repository_names: tuple[str, ...]` — new, sorted, defaults to `()`.
  Populated once per `BoardProjection` construction (repository config is
  static machine config, `repos.json`, not something that changes mid-run —
  matching how `steps`/`workflows` are also constructor-time, not
  per-snapshot, inputs).
- No change to `Task`, `BoardColumn`, `BoardTab`, or any queue/event shape.

## Interfaces

- No new HTTP endpoints. `GET /` and `GET /fragment/board` are unchanged in
  signature; `/` gains `board.repository_names` in the template context it
  already passes (`board=view.snapshot()` already carries it).
- UI flow: operator opens `/`, sees the filter bar, picks a repository and/or
  types text, cards narrow in the active tab (and every tab, when switched);
  an SSE-triggered refresh preserves both filter values.

## Dependencies and scope

- Depends on FR-1/FR-2 landing before FR-3 can render real options (the
  template needs `board.repository_names` to exist); FR-4/FR-5 are pure
  front-end and can be built against a stubbed board in isolation but should
  land together since they're one visible feature.
- **Out of scope** (per the task notes): filtering by status/outcome,
  persisting filter choice across reloads (e.g. localStorage), any filter on
  `/admin/*` pages, server-side filtering/query params, and touching
  `route()`/dispatcher/consumer.

## Rough plan

1. `ports/board.py` — add `repository_names` field to `Board`, include in
   `to_dict()`.
2. `projection.py` — `BoardProjection.__init__` takes `repository_names`,
   stores sorted tuple, `snapshot()` passes it through.
3. `app.py::build()` — thread `repository_registry.names()` (or `()`) into
   the existing `BoardProjection(...)` call.
4. `templates/board.html` — add the filter bar markup outside `#board`, add
   `applyFilters()` + wiring (input/change listeners, initial call,
   `afterSwap` hook).
5. `templates/_columns.html` — add `data-repository`/`data-search` attributes
   to `.card`.
6. `api/static/app.css` — `.filter-bar` layout rules, `.hidden-by-filter`.
7. Tests:
   - `tests/test_projection.py` — `BoardProjection` with `repository_names`
     produces a sorted tuple on `snapshot()`; default is `()`.
   - `tests/test_board_port.py` or similar — `Board.to_dict()` includes
     `repository_names`.
   - An `app.py` build test — `build(..., repository_registry=<fake with
     names ["a","b"]>)` yields a projection snapshot with those names; no
     registry yields `()`.
   - `tests/test_api_html.py` — `GET /` renders the `<select>` with the
     registry's options and an "All repositories" default; each rendered
     `.card` carries `data-repository`/`data-search`.
   - No new route tests needed for filtering itself (it's untestable without
     a browser/JS harness) — the acceptance criteria's actual filtering
     behavior (hide-on-select, hide-on-type, AND-compose, clear-restores,
     survive-SSE-swap) needs to be exercised via a real browser check with the
     `run`/manual-verification skill before calling the feature done, since
     this repo's test suite has no JS execution harness (`grep -rl
     "playwright\|selenium\|puppeteer" tests/` returns nothing — confirmed
     during planning). Note this explicitly to the implementer: write the JS
     carefully and verify interactively, don't assume unit tests alone prove
     the client-side behavior.
8. Run `.venv/bin/pytest -q` and `tests/test_architecture.py` specifically to
   confirm invariant #5/#33 still hold (no new driver import crept into
   `api/`).

## Open questions

- **Where exactly the filter bar sits relative to `#board`.** Chose "outside,
  as a sibling before `<div id="board">`" so the input's own DOM value
  survives the `hx-swap="innerHTML"` untouched (no `data-` attribute needed
  for the *values themselves*, only for the *re-application of hiding* to
  freshly swapped cards). This differs slightly from `applyActiveTab`'s
  pattern (which stores state as a `data-` attribute *inside* `#board`
  because the tab strip itself is rebuilt every swap) — noted as a deliberate
  divergence, not an oversight, in case a reviewer expects a literal copy of
  the tab pattern.
- **Whether `.card__meta`'s outcome badge text or `task.last_outcome` (raw
  value) feeds the search string.** Picked the raw value
  (`task.last_outcome`, e.g. `"request_changes"`) since it's already
  lower-case and stable; the badge's visible text is identical in this
  codebase (`{{ task.last_outcome }}` verbatim), so there's no behavioral
  difference — just noting the source of truth for the implementer.
- **Multi-word fulltext queries.** A plain substring match on the
  concatenated fields (not per-word AND) — simplest, matches the "matches
  case-insensitively against the task's visible text" wording literally.
  Defaulting to this; flagging in case the reviewer wants per-word AND
  matching instead (out of scope unless requested).
