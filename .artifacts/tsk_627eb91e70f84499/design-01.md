# Design — per-workflow board tabs

Concretizes plan-01.md (FR-1..FR-7) against the current shapes of
`ports/board.py`, `projection.py`, `ports/workflows.py`, `drivers/fs_workflows.py`,
`drivers/memory.py`, `app.py`, `api/routes.py` and the two templates. No open
question from the plan is left for implementation to resolve; each is settled
below with a concrete answer.

## UX/UI

### Wireframe

```
┌─────────────────────────────────────────────────────────────────────┐
│ harness board                                                       │
│                                                                       │
│  [ default ] [ hotfix ]                     <- tab strip (2 tasks   │
│  ─────────────────────                          in hotfix, no badge │
│                                                   needed — count is  │
│                                                   per-column already)│
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐  │
│ │ todo 1 │ │ plan 0 │ │design 1│ │ dev  0 │ │ done 3 │ │failed 0│  │
│ ├────────┤ ├────────┤ ├────────┤ ├────────┤ ├────────┤ ├────────┤  │
│ │ card   │ │        │ │ card   │ │        │ │ card   │ │        │  │
│ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘  │
│                     (panel for the active tab; "hotfix" panel       │
│                      exists in the DOM but is display:none)         │
└─────────────────────────────────────────────────────────────────────┘
```

When there are zero or one workflow tabs (plus no non-empty `unknown` tab),
the strip renders nothing — one panel, no tabs, identical to today's board.
This keeps the common single-workflow case visually unchanged (non-goal:
don't make the default case busier for a feature most single-workflow users
never trigger).

### Component hierarchy

```
board.html
 └─ #board [data-active-workflow]        (persists across SSE swaps)
     └─ _columns.html  (fragment, swapped wholesale by hx-swap="innerHTML")
         ├─ .tab-strip                    (omitted if <2 renderable tabs)
         │   └─ .tab[data-workflow]  × N  (button, one per Board.workflows
         │                                 entry that is non-empty or not
         │                                 "unknown")
         └─ .workflow-panel[data-workflow] × N
             └─ .column  × columns-in-that-tab
                 └─ .card  × tasks-in-that-column   (unchanged _task-card
                                                      markup/behavior)
```

`_task.html` (detail modal) is unchanged — it already renders
`task.workflow_template` (line 10) and detail semantics don't depend on tabs.

### Key interactions

1. **Tab click** — plain client-side `click` delegate on `.tab-strip`, no
   `hx-*` attributes. Sets `#board`'s `data-active-workflow` to the clicked
   tab's `data-workflow`, then re-runs the same "apply active tab" function
   used after an SSE swap (see below). No request is sent.
2. **SSE refresh while a non-default tab is active** — `/fragment/board`
   replaces `#board`'s children (`hx-swap="innerHTML"` touches only children,
   never the `#board` element's own attributes, so `data-active-workflow`
   survives). An `htmx:afterSwap` listener (scoped to `event.detail.target.id
   === 'board'`) re-applies visibility from the (untouched)
   `data-active-workflow` value to the freshly-swapped panels. This is the
   same pattern the file already uses for revision de-dup — extending it,
   not adding a new mechanism.
3. **First load, no prior selection** — `data-active-workflow` is rendered
   server-side by `board.html` from `Board.default_tab()` (FR-6: `"default"`
   if present, else first name alphabetically; see Component design). The
   client never has to guess the initial tab.
4. **A tab holding zero tasks** — still rendered (a workflow definition with
   no tasks yet is still a legitimate tab to switch to and watch), *except*
   the reserved `unknown` tab, which is omitted from the strip and the panel
   markup entirely when empty (FR-4's acceptance criterion) — there is no
   `unknown.json` definition backing it, so "render the empty tab anyway"
   has no natural default to fall back to and would only ever mean "GitHub
   ingestion broke," which the `failed` column across every real tab already
   surfaces.

## Component design

### `ports/workflows.py` — `WorkflowRepository.names()`

```python
class WorkflowRepository(ABC):
    @abstractmethod
    def get(self, name: str) -> Workflow: ...

    @abstractmethod
    def names(self) -> list[str]:
        """All discoverable workflow names, sorted alphabetically.

        Lenient where get() is strict: a definition that fails to parse is
        omitted, never raised. Enumeration must survive one broken file."""
```

Sorting is the repository's job (not the caller's) — every consumer of
`names()` (board wiring today, possibly a CLI listing later) gets the same
order for free, and `sorted()` next to a `set`/`dict` iteration in one
implementation only would be an easy place to regress FR-6's determinism.

- `FilesystemWorkflowRepository.names()`: `sorted(p.stem for p in
  self._root.glob("*.json") if self._is_valid(p.stem))`. Reuses `get()`'s
  validation by calling it in a `try/except WorkflowNotFound: continue` —
  no parallel parsing logic to drift out of sync with `get()`. Missing root
  directory → `[]` (mirrors `RepositoryRegistry.names()`'s "missing/unreadable
  → empty list" contract already documented in `ports/repos.py`).
- `MemoryWorkflowRepository.names()`: `sorted(self._workflows.keys())`.

### `ports/board.py` — `BoardTab`, reserved constant, `Board` reshape

```python
UNKNOWN_WORKFLOW = "unknown"
"""Reserved tab for tasks whose workflow_template names no discovered
definition. Never a real workflow's name (workflow names come from filenames,
and "unknown" collides with nothing in practice; if it ever legitimately did,
the discovered definition wins and simply gets a second, indistinguishable
population route into the same tab — an acceptable edge the plan already
flags as out of scope to guard against)."""

@dataclass(frozen=True)
class BoardTab:
    name: str
    columns: tuple[BoardColumn, ...]

    def column(self, name: str) -> BoardColumn | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "columns": [c.to_dict() for c in self.columns]}


@dataclass(frozen=True)
class Board:
    revision: int
    workflows: tuple[BoardTab, ...]

    def workflow(self, name: str) -> BoardTab | None:
        for tab in self.workflows:
            if tab.name == name:
                return tab
        return None

    def default_tab(self) -> str | None:
        """"default" if present, else the first tab alphabetically, else None
        (an empty board — no workflow definitions and no orphaned tasks)."""
        names = [tab.name for tab in self.workflows]
        if "default" in names:
            return "default"
        return names[0] if names else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "workflows": [tab.to_dict() for tab in self.workflows],
        }
```

`BoardColumn` is unchanged. `Board.column(name)` is removed — every caller
(templates, tests) now goes through a tab first, matching FR-2's note that a
flat lookup no longer makes sense once the same step name can legitimately
exist in two tabs. `default_tab()` lives on `Board`, not in the template or
`routes.py`, so JSON API consumers and the HTML both get FR-6's determinism
from one place (mirrors FR-7 — one object, no drift).

### `projection.py` — multi-workflow `BoardProjection`

```python
def __init__(self, workflows: Sequence[Workflow]) -> None:
    self._orders: dict[str, tuple[str, ...]] = {
        wf.name: column_order(wf) for wf in workflows
    }
    self._orders[UNKNOWN_WORKFLOW] = (DONE_COLUMN, FAILED_COLUMN)
    self._tasks: dict[str, Task] = {}
    self._locations: dict[str, tuple[str, str]] = {}  # task_id -> (tab, column)
    ...
```

`column_order()` (the reachability walk) is untouched — it already takes one
`Workflow` and returns one tuple; the projection now calls it once per
discovered workflow instead of once total.

`_store` changes signature to resolve the tab from the task, not from the
caller:

```python
def _store(self, column: str, task: Task) -> None:
    tab = task.workflow_template if task.workflow_template in self._orders else UNKNOWN_WORKFLOW
    order = self._orders[tab]
    if column not in order:
        return
    self._tasks[task.id] = task
    self._locations[task.id] = (tab, column)
```

`apply(column, task)` and `hydrate(...)`'s call sites (`self._store(step,
task)`, `self._store(DONE_COLUMN, task)`, etc.) are **unchanged at the call
site** — `apply`'s external signature stays `apply(self, column: str, task:
Task) -> None`, so `ProjectionSink.emit` (`drivers/projection_events.py:27`)
needs no edit. Tab resolution is an internal `_store` concern, invisible to
the event bridge — this is what FR-3 means by "resolve the tab from
`task.workflow_template`, not from whichever queue happened to deliver it":
the *column* still comes from the event's `queue` field, only the *tab* it's
filed under is now looked up from the task itself.

`snapshot()`:

```python
def snapshot(self) -> Board:
    tabs = []
    for name, order in self._orders.items():
        if name == UNKNOWN_WORKFLOW and not any(
            tab == UNKNOWN_WORKFLOW for tab, _ in self._locations.values()
        ):
            continue  # FR-4: omit the empty unknown tab
        columns = tuple(
            BoardColumn(
                name=col,
                tasks=tuple(sorted(
                    (t for tid, t in self._tasks.items()
                     if self._locations[tid] == (name, col)),
                    key=lambda t: (t.created, t.id),
                )),
            )
            for col in order
        )
        tabs.append(BoardTab(name=name, columns=columns))
    tabs.sort(key=lambda tab: tab.name)
    return Board(revision=self._revision, workflows=tuple(tabs))
```

Ordering `self._orders` as a plain `dict` built from `names()` (already
alphabetical) plus `unknown` appended last, then `tabs.sort()` at the end,
keeps `unknown` from always sorting first/last by accident and gives FR-6's
"plain alphabetical" tab order regardless of insertion order — cheap since
tab counts are small (single-digit workflows in practice).

`get(task_id)` is unchanged (still a flat task-id lookup — a task id is
unique across the whole board regardless of which tab it's filed under).

### `app.py` — `build()` wiring

```python
workflows = FilesystemWorkflowRepository(layout.workflows)
workflow = workflows.get(workflow_name)   # unchanged: drives dispatch/queues

discovered: list[Workflow] = []
for name in workflows.names():
    try:
        discovered.append(workflows.get(name))
    except WorkflowNotFound:
        continue  # names() should already have filtered this out; defensive
                   # only against a definition deleted between the two calls
if workflow.name not in {wf.name for wf in discovered}:
    discovered.append(workflow)  # active workflow always gets a tab, even
                                   # if e.g. --workflow points outside layout.workflows

projection = BoardProjection(discovered)
```

The last three lines are new versus the plan's rough step 6: without them, a
`--workflow` pointed at a definition that `names()` wouldn't enumerate (an
unusual but currently-possible path, since `get()` and `names()` both key off
`layout.workflows` today so this is mostly a defensive belt-and-braces line)
would dispatch tasks with no tab to land in. Cheap to include, closes the gap.
`step_queues` construction (`workflow.steps()`) is unchanged — invariant "only
the active `--workflow` has live step queues" from the plan's non-goal holds.

### `api/routes.py` and templates

No route signatures change. `board()` and `fragment_board()` keep calling
`view.snapshot().to_dict()` / passing `{"board": view.snapshot()}` — only the
shape of what's inside changes (FR-7 holds structurally, not just by
assertion).

`board.html`:

```html
<div id="board"
     data-revision="{{ board.revision }}"
     data-active-workflow="{{ board.default_tab() or '' }}"
     hx-get="/fragment/board" hx-trigger="sse:board" hx-swap="innerHTML">
  {% include "_columns.html" %}
</div>
```

```js
function applyActiveTab(boardEl) {
  var active = boardEl.getAttribute('data-active-workflow');
  boardEl.querySelectorAll('.tab').forEach(function (el) {
    el.classList.toggle('active', el.dataset.workflow === active);
  });
  boardEl.querySelectorAll('.workflow-panel').forEach(function (el) {
    el.style.display = (el.dataset.workflow === active) ? '' : 'none';
  });
}
document.getElementById('board').addEventListener('click', function (event) {
  var tab = event.target.closest('.tab');
  if (!tab) return;
  var boardEl = document.getElementById('board');
  boardEl.setAttribute('data-active-workflow', tab.dataset.workflow);
  applyActiveTab(boardEl);
});
document.body.addEventListener('htmx:afterSwap', function (event) {
  if (event.detail.target.id === 'board') { applyActiveTab(event.detail.target); }
  if (event.detail.target.id === 'detail') { document.getElementById('detail').showModal(); }
});
applyActiveTab(document.getElementById('board'));
```

The existing revision de-dup IIFE is untouched; this listener is added
alongside it, and the pre-existing unscoped `htmx:afterSwap` listener for the
modal gains the `target.id === 'board'` branch shown above (folded into the
same listener rather than a second one, since both need to inspect
`event.detail.target`).

`_columns.html`:

```html
<div class="board-wrap">
  {% if board.workflows|length > 1 %}
  <div class="tab-strip">
    {% for tab in board.workflows %}
    <button class="tab" data-workflow="{{ tab.name }}">{{ tab.name }}</button>
    {% endfor %}
  </div>
  {% endif %}
  {% for tab in board.workflows %}
  <div class="workflow-panel" data-workflow="{{ tab.name }}">
    <div class="board">
      {% for column in tab.columns %}
      <div class="column">
        <h2>{{ column.name }} <span class="count">{{ column.tasks|length }}</span></h2>
        {% for task in column.tasks %}
        {# ... existing .card markup, unchanged ... #}
        {% endfor %}
      </div>
      {% endfor %}
    </div>
  </div>
  {% endfor %}
</div>
```

The `.card` block's markup/logic is a byte-for-byte carry-over from today's
`_columns.html` — no behavioral change to task cards, just re-nested one
level under `.workflow-panel`. `display: none` (not removing the DOM node) is
what makes the "task moves column" SSE acceptance case (FR-5) work: the
inactive panel still receives the swap, it's just not shown, so no client
state is lost by hiding rather than unmounting.

Tab strip is gated on `board.workflows|length > 1`, not "non-empty" — a
single discovered workflow (the common case today) renders zero tabs, one
visible panel, identical DOM shape to the pre-tabs board (bar the added-but-
inert `.workflow-panel` wrapper div).

## Data schemas

### `GET /api/board` (JSON, breaking per FR-7/plan's Interfaces section)

Before:
```json
{"revision": 12, "columns": [{"name": "plan", "tasks": [...]}]}
```

After:
```json
{
  "revision": 12,
  "workflows": [
    {
      "name": "default",
      "columns": [
        {"name": "todo", "tasks": []},
        {"name": "plan", "tasks": [{"id": "tsk_1", "workflowTemplate": "default", "...": "..."}]},
        {"name": "design", "tasks": []},
        {"name": "done", "tasks": []},
        {"name": "failed", "tasks": []}
      ]
    },
    {
      "name": "hotfix",
      "columns": [
        {"name": "todo", "tasks": []},
        {"name": "patch", "tasks": []},
        {"name": "done", "tasks": []},
        {"name": "failed", "tasks": []}
      ]
    }
  ]
}
```

`unknown` tab, only present when non-empty:
```json
{
  "name": "unknown",
  "columns": [
    {"name": "done", "tasks": []},
    {"name": "failed", "tasks": [{"id": "tsk_9", "workflowTemplate": "ghost", "lastOutcome": "failed", "history": [{"...": "reason: workflow 'ghost' not found"}]}]}
  ]
}
```

Task shape inside a column (`task.to_dict()`) is completely unchanged —
tabs only regroup existing tasks, they don't add or rename task fields.

### `GET /fragment/board` (HTML)

Same `Board` object as above, rendered per Component design's template
changes — no separate schema, per FR-7.

### No new event payloads

The board-mutating event already carries `{"task": <dict>, "queue": <str>}`
(invariant 7); tab resolution reads `task["workflowTemplate"]` off the
existing payload via `Task.from_dict` in `ProjectionSink.emit`, so no event
producer changes.

## Answers to the plan's open questions

- **Concurrent execution of multiple workflows**: confirmed out of scope.
  This design only touches the read side; `step_queues`/dispatch stay keyed
  to the single active `--workflow`.
- **Fallback tab contents**: confirmed `(done, failed)` for `unknown`, fixed
  as a literal in `BoardProjection.__init__` (`self._orders[UNKNOWN_WORKFLOW]
  = (DONE_COLUMN, FAILED_COLUMN)`), not derived from any workflow.
- **Discovery cadence**: confirmed one-time scan in `app.py build()`, no
  periodic re-scan, no file-watcher.
- **Tab-switch mechanism**: confirmed client-side-only via
  `data-active-workflow` + delegated click handler; no `?workflow=` query
  param, no new route, added to the design above.
