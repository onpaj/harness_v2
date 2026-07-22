# Architecture assessment — per-workflow board tabs

Reviewed against the current code (`ports/board.py`, `projection.py`,
`ports/workflows.py`, `drivers/fs_workflows.py`, `drivers/memory.py`, `app.py`,
`api/routes.py`, `templates/{board,_columns}.html`, `test_architecture.py`) and
against `plan-01.md` / `design-01.md`. Verdict: **the design is sound and ready
to implement as written, with one required fix** (the `unknown` tab's column
set must include `todo`, not just `done`/`failed` — see Risks). Everything
else below is confirmation, sequencing, and blast-radius bookkeeping for the
implementer, not a redesign.

## Alignment with existing patterns and integration points

- **`names()` beside a strict `get()` is an established shape, not a new
  one.** `ports/repos.py::RepositoryRegistry` already has exactly this pair
  (`resolve` strict / `names` lenient, "missing/unreadable → empty list").
  `WorkflowRepository.names()` should copy that docstring contract verbatim —
  same lenient/strict split, same empty-list-on-missing-root behavior. This is
  not a judgment call; it's reuse of a decision already made once in this
  codebase.
- **`BoardTab` is `BoardColumn` at one more level of nesting** — same frozen
  dataclass, same `name`/`to_dict()` shape, same flat linear-scan lookup
  method. No new pattern is introduced; `Board` changes from "holds columns"
  to "holds tabs that hold columns," structurally identical to how a column
  holds tasks.
- **Invariant 5 holds by construction, not by discipline.** `projection.py`
  keeps importing only `harness.models` (`Workflow`) and `harness.ports.*` —
  `Sequence[Workflow]` replacing a single `Workflow` in the constructor
  changes arity, not what's imported. `api/` is untouched at the import level:
  `routes.py` still only calls `view.snapshot()` and passes the result to
  Jinja. `test_projection_does_not_import_drivers` and
  `test_api_does_not_import_drivers` (`test_architecture.py:197`, `:205`)
  need no new cases — they already cover this file-by-file.
- **Invariant 7 holds unchanged.** `ProjectionSink.emit` (`drivers/
  projection_events.py:16`) still reads `fields["task"]` and `fields["queue"]`
  and calls `projection.apply(column, task)` with the same two-argument
  signature. Tab resolution is correctly placed *inside* `_store`, keyed off
  `task.workflow_template`, which already rides on every task event payload
  via `Task.to_dict()`/`from_dict()` (`models.py:106`, `:120`) — no event
  producer anywhere needs to change. This is the design's most important
  property: it turns a UI feature into a pure read-side change by construction,
  not by care.
- **`app.py` is the only file allowed to know about `FilesystemWorkflowRepository`
  and both `workflows.get()` and `workflows.names()`**, consistent with "wiring
  belongs exclusively in `app.py`" (invariant 1) and `test_only_app_and_cli_
  wire_drivers`. The dispatcher's single active `workflow` (driving
  `step_queues`) and the projection's `discovered` list (driving tabs) are two
  independent reads off the same repository — correctly not entangled with
  each other or with `Dispatcher`/`Consumer`.
- **Dispatcher/consumer are provably untouched.** `dispatcher.py:60` already
  does `self._workflows.get(task.workflow_template)` per-task (not once at
  startup) — this task adds a reader of `WorkflowRepository.names()`
  (`app.py` only) without adding a second call site inside orchestration.
  `router.py` (pure function, invariant 4) isn't touched at all.

## Proposed architecture

No deviation from `design-01.md`'s component shapes. Summarizing the
decisions and confirming each is the right one for this codebase, not just *a*
workable one:

1. **`WorkflowRepository.names()`, lenient.** Alternative considered and
   rejected: let `BoardProjection`/`app.py` glob the directory itself. Wrong
   layer — `FilesystemWorkflowRepository` already owns the filename↔definition
   convention (`invalid_workflow_name`, the `.json` extension, `get()`'s
   validation). Duplicating that logic in `app.py` to enumerate would drift
   from `get()`'s validation the first time one of them changes. Routing
   `names()` through `get()`'s own `try/except WorkflowNotFound` (as
   `design-01.md` specifies) is correct: one validator, two callers.
2. **`Board.workflows: tuple[BoardTab, ...]` replacing `Board.columns`, with
   `Board.column()` removed outright.** Alternative considered: keep a
   deprecated flat `columns` property for compatibility. Rejected — this is
   an internal read model with no external contract (plan's Interfaces
   section already calls this out), and a compatibility shim here would mean
   two ways to ask "what's in column X" that silently disagree the moment a
   second workflow exists. Removing it is the correct move; it also forces
   every caller (tests included) to state which tab it means, which is the
   whole point of the feature.
3. **Tab resolution lives inside `BoardProjection._store`, not at the call
   site.** This is the load-bearing decision that keeps the change additive.
   The alternative — having `hydrate()`/`apply()` callers pass the tab
   explicitly — would require `ProjectionSink.emit` to compute the tab and
   thread it through `apply()`'s signature, which breaks invariant 7's
   "an event carries `task` and `queue`, nothing more" framing and would
   touch a file (`drivers/projection_events.py`) that has no reason to change
   for a UI-only feature. Confirmed correct as specified.
4. **`UNKNOWN_WORKFLOW` fallback tab, one-time discovery at `app.py build()`,
   client-side-only tab switch.** All three are the right defaults for this
   phase and match the plan's own reasoning; see Risks for the one place the
   `unknown` tab's column set needs to change from what's written.
5. **Sort tabs alphabetically in `snapshot()`, not by insertion order.**
   Correct given `names()` is already sorted but `unknown` is appended after
   the fact — sorting once at the end is simpler than keeping two sorted
   sources merged, and cheap at realistic tab counts (single digits).

### Data flow (unchanged shape, now parameterized by tab)

```
FilesystemWorkflowRepository.names()          (app.py, once, at build())
        │
        ▼
[Workflow, Workflow, ...]  ──► BoardProjection.__init__
        │                            │
        │                            ▼
        │                   {name: column_order(wf)} + {"unknown": (done, failed)}
        │
        ▼
hydrate()/apply(column, task)  ──►  _store: tab = orders-lookup(task.workflow_template)
                                            └─► (tab, column) → self._locations[task.id]
        │
        ▼
snapshot() ──► Board(revision, workflows=[BoardTab(name, columns=[...]), ...])
        │
        ├──► GET /api/board            → Board.to_dict()
        └──► GET /fragment/board       → _columns.html (tab strip + panels)
```

The only new edge in this diagram versus today is the `names()` call at the
top, which happens once, outside the hot path — `apply()` (called once per
task-movement event, on the hot path) does no filesystem or lookup work beyond
a dict access, same as today.

## Implementation guidance

Follow the plan's rough-plan ordering (1→9); it is already dependency-correct
(port → drivers → board port → projection → wiring → templates → tests). Two
sequencing notes:

- **Do `ports/board.py` (`BoardTab`, `Board` reshape) before touching
  `projection.py`.** `projection.py` imports `Board`/`BoardColumn` from
  `ports/board.py`; landing the type first means `projection.py`'s edit
  compiles against its final dependency, not an intermediate one.
- **Update `app.py`'s `Harness` construction last among source changes, right
  before templates.** `Harness.__init__` (`app.py:96`, field `projection:
  BoardProjection`) doesn't change type — it stays `BoardProjection` — so
  `cli.py`'s `view=harness.projection` (`cli.py:709`) needs no edit. Confirm
  this with a grep after the change rather than assuming it; the type name not
  changing is easy to verify but easy to get complacent about.

### Contracts to hold exactly as `design-01.md` specifies

- `BoardProjection.apply(self, column: str, task: Task) -> None` — signature
  unchanged. `ProjectionSink` must not need an edit; if an implementer finds
  themselves wanting to change this signature, that's a signal the tab
  resolution has leaked out of `_store` and belongs back inside it.
  `apply`'s job is unchanged: "some queue named `column` now holds `task`" —
  the projection alone decides which tab that implies.
- `WorkflowRepository.names()` returns `list[str]`, sorted, lenient. Both
  implementations (`FilesystemWorkflowRepository`, `MemoryWorkflowRepository`)
  must return the same order given the same underlying names — a test should
  assert this directly (construct both with the same 3 names, assert equal
  `names()` output) since nothing else enforces it structurally.
- `Board.default_tab()` is the single source of the "`default` if present,
  else alphabetically first" rule — it must not be reimplemented in
  `routes.py` or in the Jinja template. This is what keeps FR-7 (JSON/HTML
  parity) true structurally rather than by two independent implementations
  agreeing today and drifting tomorrow.

## Risks and mitigations

1. **Required fix — the `unknown` tab must include `todo`, not just
   `done`/`failed`.** `design-01.md` fixes
   `self._orders[UNKNOWN_WORKFLOW] = (DONE_COLUMN, FAILED_COLUMN)`. Walk the
   failure path: a task is submitted with a typo'd/deleted `workflow_template`
   and lands in the inbox (`status=None`) before the dispatcher's first tick
   fails it. If the harness restarts in that window, `hydrate()` calls
   `self._store(TODO_COLUMN, task)` for it (`projection.py:81`, unchanged
   logic). With `unknown`'s order fixed at `(done, failed)`, `_store` finds
   `"todo" not in order` and **silently drops the task** — it appears in no
   tab at all until the dispatcher gets to it and moves it to `failed`. That
   directly contradicts FR-4's stated guarantee ("never silently dropped")
   for the one code path (`hydrate`, not `apply`) where the gap is actually
   observable, since at runtime the dispatcher fails such a task before the
   projection ever gets a chance to show it mid-flight in `todo`.
   **Mitigation**: `self._orders[UNKNOWN_WORKFLOW] = (TODO_COLUMN, DONE_COLUMN,
   FAILED_COLUMN)`. One-line change, exactly the escape hatch `design-01.md`
   already names ("if design disagrees, the fix is a one-line change to the
   precomputed column tuple") — this review is exercising that hatch. Add a
   `hydrate()`-specific test: inbox task with an unrecognized
   `workflow_template`, before any dispatch → appears under `unknown` →
   `todo`.
2. **Test blast radius is wider than `plan-01.md` step 8's example list.**
   Grepping the current suite for `Board(`, `BoardColumn(`, `.column(`, and
   `BoardProjection(` turns up direct construction/assertions in
   `test_api_artifacts.py`, `test_api_sse.py`, `test_app.py`, `test_cli.py`
   in addition to the six files the plan named. All of these break at import/
   construction time the moment `Board.columns`→`Board.workflows` and
   `Board.column()` is removed — this is expected fallout from a deliberate
   breaking change (FR-7's "breaking, but acceptable" call), not a design
   flaw, but it means step 8 is bigger than the plan's list suggests.
   **Mitigation**: budget for it up front; run
   `grep -rn "BoardProjection(\|Board(\|BoardColumn(\|\.column(" tests/` after
   the port/projection changes land and treat every hit as a required edit,
   not just the six named files. Most edits are mechanical
   (`BoardProjection(WORKFLOW)` → `BoardProjection([WORKFLOW])`,
   `board.column("x")` → `board.workflow("default").column("x")`).
3. **`FilesystemWorkflowRepository.names()` re-running `get()`'s full
   validation per file is O(workflow count) filesystem reads at `app.py
   build()` time.** Negligible at realistic scale (single-digit workflow
   files) and happens once at startup, not per-request — not worth a cache.
   Flagging only so a future reviewer doesn't mistake "re-parses every file"
   for an oversight; it's the deliberate "no parallel validation logic"
   tradeoff `design-01.md` already argues for.
4. **A workflow file added or edited while the harness is running has no
   effect on the board until restart** (discovery is one-time, per the design's
   answered open question). This matches today's behavior for the single
   active `--workflow` and is explicitly scoped out — not a regression, just
   worth restating so it isn't rediscovered as a "bug" during manual smoke
   testing (plan step 9).
5. **`BoardTab.column()` and `Board.workflow()` are linear scans over small
   tuples** (single-digit tab/column counts) — no risk at this scale, not
   worth a dict. Noted only to preempt a premature-optimization detour during
   implementation.

## Prerequisites before implementation

None blocking. The design doc has already answered every open question the
plan raised, `WorkflowRepository`/`FilesystemWorkflowRepository`/
`MemoryWorkflowRepository` are exactly where the design says they are, and
`test_architecture.py`'s existing invariant tests need no new cases (only the
ones that construct `Board`/`BoardProjection` directly need updating, per
Risk 2). The single required change before writing code is the one-line
`unknown` tab column-set fix in Risk 1 — apply it as an amendment to
`design-01.md`'s `projection.py` section, then proceed directly to the plan's
rough-plan steps 1–9 in order.
