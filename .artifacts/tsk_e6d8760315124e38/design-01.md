# Design — repository select filter + fulltext filter on the board UI

Builds on `.artifacts/tsk_e6d8760315124e38/plan-01.md`. That plan is already
concrete about which files change and why; this document nails down the exact
shapes (markup, data attributes, CSS selectors, JS functions, data schema) so
implementation is a transcription exercise, not a design exercise.

## UX/UI

### Wireframe

```
┌─ Board ─────────────────────────────────────────────── [+ Add issue] ─┐
│                                                                        │
│  Repository: [ All repositories        ▾]   Filter: [________________]│  <- .filter-bar (new)
│                                                                        │
│  [development] [resolver] [heal]                <- .tab-strip (unchanged)
│  ┌─ todo (2) ──┐ ┌─ design (1) ─┐ ┌─ development (3) ─┐ ┌─ review (0) ─┐
│  │ card        │ │ card         │ │ card              │ │ No tasks    │
│  │ card        │ │              │ │ card              │ │             │
│  │             │ │              │ │ card              │ │             │
│  └─────────────┘ └──────────────┘ └───────────────────┘ └─────────────┘
│                                                                        │
│  harness 0.21.1 · built ...                                           │
└────────────────────────────────────────────────────────────────────────┘
```

Typing `db` into the fulltext box or picking `other-repo` from the select
hides non-matching cards in place (no layout reflow beyond the hidden cards
disappearing) and rewrites each visible column's count. An SSE-triggered
`/fragment/board` swap re-renders `#board`'s innerHTML with fresh cards
carrying no filter state; the existing `htmx:afterSwap` hook reapplies both
the active-tab visibility and the filter in the same tick, so the operator
never sees a flash of unfiltered cards.

### Component hierarchy

```
board.html
├── .page-header                      (unchanged)
├── .filter-bar                       (NEW — sibling of #board, survives SSE swap)
│   ├── <select id="filter-repo">     (NEW)
│   └── <input id="filter-text">      (NEW)
├── #board  [hx-get swap target]
│   └── _columns.html                 (server-rendered fragment, swapped wholesale)
│       ├── .tab-strip
│       └── .workflow-panel (× N)
│           └── .column (× M)
│               └── .card (× K)       (gains data-repository/data-search — see Data schemas)
└── <script>                          (gains applyFilters(), two listeners, one afterSwap call)
```

The filter bar lives **outside** `#board` deliberately: `hx-swap="innerHTML"`
replaces only `#board`'s children, so a `<select>`/`<input>` placed as a
sibling keeps its live DOM value (current selection, cursor position, focus)
across every SSE refresh with no `data-` round-trip needed for the values
themselves. This differs from `applyActiveTab`'s pattern (state stored via
`data-active-workflow` *inside* `#board`) only because the tab strip itself
is rebuilt every swap and needs somewhere durable to read its own state from;
the filter controls are already durable by sitting outside the swap target.
What *does* need reapplying after a swap is the *effect* of the filter on the
freshly rendered cards — that's `applyFilters()`, called from the same
`htmx:afterSwap` handler that already calls `applyActiveTab()`.

### Key interactions

1. **Type in the fulltext box** → `input` event → `applyFilters()` runs
   synchronously → matching cards' `hidden-by-filter` class is removed,
   non-matching cards' is added → column counts recompute from the visible
   set.
2. **Pick a repository** → `change` event → same `applyFilters()` path.
3. **Clear both** (empty select value, empty text) → every card's `repoOk`
   and `textOk` are trivially true → `hidden-by-filter` removed from all →
   full board restored, counts back to the server-rendered totals.
4. **SSE fires `/fragment/board`** → htmx swaps `#board.innerHTML` → fresh
   `.card`s appear with no `hidden-by-filter` class and fresh
   `data-repository`/`data-search` → `htmx:afterSwap` calls
   `applyActiveTab(target)` then `applyFilters(target)` → the operator's
   still-live `<select>`/`<input>` values (never touched by the swap) are
   reapplied against the new DOM in the same tick.
5. **Switch workflow tab** → `applyActiveTab` toggles `.workflow-panel`
   display; `applyFilters` had already hidden/shown cards across *all* panels
   (not just the active one), so counts in the panel the operator switches
   *to* are already correct — no re-run needed on tab switch, though it's
   harmless if triggered.

## Component design

### `BoardProjection` / `Board` (read model)

- **Responsibility boundary**: `Board` is a pure snapshot of view data handed
  to templates. It must not gain any *decision* — repository names are data
  for a `<select>`'s options, never consulted by anything that routes a task.
  This keeps invariant #8 intact: the projection holds the list, nobody
  reads it back to decide placement.
- **Interface**: `BoardProjection.__init__(steps, workflows=(), repository_names=())`.
  Stored once at construction (mirrors `steps`/`workflows`, which are also
  constructor-time inputs, not re-read per snapshot) since the registry
  (`repos.json`) is static machine config for the life of a run.
  `snapshot()` copies the stored sorted tuple straight onto the `Board` it
  builds — no per-call recomputation, no I/O.
- **Why constructor-time, not a live port reference**: `BoardProjection`
  already treats `workflows`/`steps` this way. Giving it a live
  `RepositoryRegistry` reference instead would (a) require `projection.py` to
  import `ports/repos.py` — fine, that's a port, not a driver — but (b) adds
  no value here since the set of registered repositories does not change
  within a process lifetime, and (c) breaks the existing pattern of
  `BoardProjection` holding only plain data for anything that isn't the live
  task/queue state it's specifically built to project.

### `app.py::build()` wiring

- **Responsibility**: `build()` already receives `repository_registry:
  RepositoryRegistry | None` for `VerifyBehavior`/`known_repositories`. It
  gains one more consumer of that same parameter: threading
  `repository_registry.names()` (or `()` when `None`) into the
  `BoardProjection(...)` call. No new parameter, no new port method, no new
  import — `RepositoryRegistry` is already importable in `app.py` (it wires
  drivers), and this is exactly the shape `ProcessAdmin.repository_names()`
  already established as this codebase's precedent for "surface the
  registry's names through a read/admin port without leaking the driver
  itself into `api/`" (`drivers/fs_processes.py:531`,
  `FilesystemProcessAdmin.repository_names`).
- **Boundary respected**: `api/routes.py` needs zero changes. It already
  passes `view.snapshot()` into the template as `board`; `board.repository_names`
  simply exists on that object once `app.py` threads it through. `api/`
  continues to import no driver (invariant #5/#33) — the registry itself
  never crosses into `api/`, only the resolved tuple of strings, already
  baked into the `Board` object by the time `routes.py` touches it.

### Client-side filter script (`board.html`'s `<script>` block)

- **Responsibility**: a single pure function, `applyFilters(boardEl)`, that
  reads the two filter control values, walks every `.card` under `boardEl`,
  toggles `hidden-by-filter`, then walks every `.column` and rewrites its
  count/empty-state. It has no knowledge of tabs, SSE, or htmx — it is called
  by the three places that need it (initial load, control `input`/`change`,
  post-swap), the same separation `applyActiveTab` already models.
- **Not a class, no state beyond the DOM itself**: filter values live in the
  `<select>`/`<input>` elements (which persist across swaps, per the UX
  section above); `applyFilters` needs no closure variable to remember them
  between calls. This keeps the addition a single function plus three call
  sites, matching the existing script's idiom rather than introducing a new
  abstraction.

### `_columns.html` (server-rendered fragment)

- **Responsibility**: unchanged structurally — still renders exactly the
  columns/cards it does today. Its only addition is two `data-*` attributes
  per `.card`, computed once per render in Jinja so the client-side script
  does a plain, already-lower-cased substring check with no per-keystroke
  normalization logic duplicated in JS.

## Data schemas

### `Board.repository_names` (new field)

```python
@dataclass(frozen=True)
class Board:
    revision: int
    workflows: tuple[BoardTab, ...]
    repository_names: tuple[str, ...] = ()   # NEW, sorted, defaults to ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "workflows": [tab.to_dict() for tab in self.workflows],
            "repository_names": list(self.repository_names),   # NEW
        }
```

- Sorted so the `<select>`'s option order is stable and alphabetic
  regardless of `repos.json` file order.
- Defaults to `()` so every existing `Board(...)` construction site (tests,
  anything not updated) keeps compiling — matches the "absent key behaves
  exactly as before" leniency this codebase applies to every optional field
  (`Workflow.max_parallel`, `BehaviorResult.tokens`, etc.).

### `.card` data attributes (new)

Rendered once per card in `_columns.html`:

```html
<div class="card{{ ... existing state classes ... }}"
     data-repository="{{ task.repository or '' }}"
     data-search="{{ (task.data.title or task.id) ~ ' ' ~ task.id ~ ' ' ~ (task.repository or '') ~ ' ' ~ (task.worktree or '') ~ ' ' ~ (task.last_outcome or '') | lower }}"
     hx-get="/fragment/task/{{ task.id }}" ...>
```

- `data-repository`: raw repository name (or `""` for a repository-less
  task), compared with `===` against the `<select>`'s value — an exact
  match, not substring, since repositories are a closed, predefined set.
- `data-search`: a single lower-cased, space-joined blob of title, task id,
  repository, worktree and last outcome. Lower-cased server-side in Jinja
  (`| lower` filter) so `applyFilters` only lower-cases the *typed* query,
  never the DOM content, keeping the per-keystroke work O(query length) not
  O(cards × content length twice).

### Client-side filter algorithm (pseudocode, not yet code — for the
implementer to transcribe)

```js
function applyFilters(boardEl) {
  var repo = document.getElementById('filter-repo').value;
  var text = document.getElementById('filter-text').value.trim().toLowerCase();
  boardEl.querySelectorAll('.card').forEach(function (card) {
    var repoOk = !repo || card.dataset.repository === repo;
    var textOk = !text || card.dataset.search.indexOf(text) !== -1;
    card.classList.toggle('hidden-by-filter', !(repoOk && textOk));
  });
  boardEl.querySelectorAll('.column').forEach(function (column) {
    var total = column.querySelectorAll('.card').length;
    var visible = column.querySelectorAll('.card:not(.hidden-by-filter)').length;
    var countEl = column.querySelector('.column__count');
    if (countEl) { countEl.textContent = visible; }
    column.classList.toggle('column--empty', visible === 0);
    // .column__empty ("No tasks") only exists server-side when the column had
    // zero cards to begin with; a column with cards all filtered out needs no
    // new placeholder element — .column--empty's dimmed-header CSS already
    // gives it the right "nothing to see here" affordance without lying about
    // there being literally no tasks server-side.
  });
}
```

Call sites (three, mirroring `applyActiveTab`'s three):
1. `applyFilters(document.getElementById('board'))` once at script load,
   right after the existing `applyActiveTab(...)` call.
2. `filter-repo`'s `change` listener and `filter-text`'s `input` listener,
   both calling `applyFilters(document.getElementById('board'))`.
3. Inside the existing `htmx:afterSwap` handler, immediately after
   `applyActiveTab(event.detail.target)`, when `event.detail.target.id ===
   'board'`.

### CSS additions (`app.css`)

```css
.filter-bar {
  display: flex; align-items: center; gap: 12px;
  margin: 0 2px 16px;                 /* matches .page-header's own margin */
}
.filter-bar select, .filter-bar input[type="search"] {
  /* reuse the existing input[type="text"] sizing rule rather than a new one */
}
.card.hidden-by-filter { display: none; }
```

No new custom properties or color tokens — the filter bar borrows
`.page-header`'s spacing rhythm and the sheet's existing form-control sizing
(`input[type="text"]`), and `hidden-by-filter` is a plain `display: none`,
the simplest possible hide mechanism, consistent with the `.is-failed`/
`.is-working` state-class idiom already used on `.card`.

## Non-functional constraints re-affirmed by this design

- **No new HTTP surface.** `/fragment/board` and `/api/events` are untouched;
  filtering is 100% client-side against already-delivered markup.
- **`api/` imports no driver.** `routes.py` is unchanged; the repository list
  arrives pre-baked on the `Board` object `app.py` already builds.
- **Routing untouched.** `repository` is read only inside Jinja (rendering a
  `data-*` attribute) and inside the projection's plain-data field — never
  compared against for placement, so invariant #8 holds unchanged.

## Test plan

- `tests/test_board_port.py` — `Board(...).to_dict()` includes
  `repository_names`; default (field omitted) is `()`.
- `tests/test_projection.py` — `BoardProjection(steps=(), workflows=(),
  repository_names=["b", "a"]).snapshot().repository_names == ("a", "b")`;
  omitting the argument yields `()`.
- `tests/test_app.py` — a `build(..., repository_registry=<fake registry with
  names ["b", "a"]>)` call yields `harness.projection.snapshot().repository_names
  == ("a", "b")`; a `build()` call with no registry yields `()`.
- `tests/test_api_html.py` — `GET /` renders a `<select id="filter-repo">`
  with an "All repositories" option plus one `<option>` per configured
  repository, and each rendered `.card` carries non-empty
  `data-repository`/`data-search` attributes reflecting that task.
- No automated test can exercise the actual hide/show/count-recompute
  behavior (no JS execution harness exists in this suite — confirmed during
  planning: `grep -rl "playwright\|selenium\|puppeteer" tests/` is empty).
  That behavior must be checked interactively (e.g. via the `run` skill) once
  implemented: type into the fulltext box, pick a repository, confirm counts
  match visible cards, clear both, and force an SSE refresh mid-filter to
  confirm the filter values and their effect both survive the swap.
- `tests/test_architecture.py` run as part of the full suite to confirm no
  new driver import crept into `api/` or `projection.py`.

## Open questions carried from the plan (unchanged, for the implementer's awareness)

- Filter bar placement (outside `#board`) is a deliberate divergence from
  `applyActiveTab`'s in-`#board` `data-` pattern, not an oversight — see the
  Component hierarchy section above for why the two need different
  mechanisms.
- Fulltext matching is a single substring check against the concatenated
  field blob (not per-word AND) — simplest reading of "matches
  case-insensitively against the task's visible text."
