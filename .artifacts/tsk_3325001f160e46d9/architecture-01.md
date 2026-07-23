# Architecture assessment: serve multiple workflows in a single running harness

Input: `plan-01.md` (scope, FR-1..FR-6) and `design-01.md` (component design,
data schemas). This document is the architectural sign-off on that design —
verified directly against the current source (`app.py`, `dispatcher.py`,
`projection.py`, `ports/workflows.py`, `drivers/fs_workflows.py`, `cli.py`,
`tests/test_architecture.py`) rather than taken on faith. Where I diverge from
design-01 or tighten an underspecified point, it's called out explicitly.
**Verdict: build design-01 as written**, with three small refinements below.
No open question blocks starting implementation.

## Alignment with existing patterns

I read the actual code the plan/design describe, not just their prose. Every
load-bearing claim holds:

- `Dispatcher.tick()` (`dispatcher.py:60`) already does
  `self._workflows.get(task.workflow_template)` and already funnels
  `WorkflowNotFound` into `self._fail(task, str(error))` (`dispatcher.py:61-63`).
  **No dispatcher change is needed** — `ServedWorkflowRepository` only has to
  raise the right exception type, which it does by construction (it subclasses
  `WorkflowRepository` and reuses `WorkflowNotFound`).
- `app.build()` (`app.py:200-347`) is confirmed single-workflow throughout:
  one `workflow_name: str` param, one `workflows.get(workflow_name)` call
  (`app.py:234`), `step_queues` built from `workflow.steps()` alone
  (`app.py:254-259`), `BoardProjection(workflow)` (`app.py:236`), and
  `Harness.workflow: Workflow` (`app.py:90/106`) used once at
  `Harness.run()` for the `started` event (`app.py:166`) and twice in
  `cli.py._init` (`cli.py:103,107`) and once in `tests/test_app.py:67`. That's
  the complete set of `.workflow` readers — design-01's "two readers" undercounts
  by one (`_init` reads it twice, at 103 and 107), but the fix is the same
  mechanical swap either way.
- `RepositoryRegistry.names()` (`ports/repos.py:23-26`) and its filesystem
  implementation (`drivers/fs_repos.py:42-49`, returns `[]` on a missing/broken
  config) are the exact precedent design-01 mirrors for
  `WorkflowRepository.names()`. Good precedent to copy — same "lenient
  enumeration, strict resolution" split, same codebase idiom.
- `tests/test_architecture.py` is the enforcement mechanism for every
  structural invariant this change must respect. I checked each one this
  design touches:
  - `test_only_app_and_cli_wire_drivers` (line 126) — `ServedWorkflowRepository`
    **must** live under `drivers/` (design-01 places it in `drivers/fs_workflows.py`,
    correct) and must be instantiated only in `app.py`/`cli.py`.
  - `test_orchestration_does_not_import_drivers` (line 40) — `dispatcher.py`
    keeps importing only `ports/workflows.py`; it receives the wrapped
    repository through the existing `WorkflowRepository` port parameter, no
    new import needed. Confirmed the design doesn't add one.
  - `test_projection_does_not_import_drivers` (line 197) — `projection.py`
    takes `Sequence[Workflow]`, still only `harness.models`/`harness.ports.*`.
  - No test currently pins `Harness.workflow` or `app.build()`'s second
    parameter name, so the rename to `workflows` is a pure internal-API change
    caught by the type checker and the two call sites above, not a hidden
    architecture-test failure.
- CLI style precedent: I checked for `argparse.add_mutually_exclusive_group`
  anywhere in `cli.py` — **none exists**. Every other startup validation error
  in `_run`/`_init` is a manual `if`, `print(..., file=sys.stderr)`,
  `return 2` (e.g. `cli.py:84-86` for `invalid_workflow_name`,
  `cli.py:682-684` for `WorkflowNotFound`). Design-01's
  `_resolve_served_workflows` helper follows that idiom rather than reaching
  for argparse's mutual-exclusion machinery — **correct call, keep it
  manual**, don't introduce a second validation style into this file.
- Test-count sanity check: `BoardProjection(` appears in `tests/test_projection.py`
  (15), `test_projection_events.py` (1), `test_api_sse.py` (6), `test_cli.py` (1),
  plus the one production site in `app.py` — 23 test call sites, matching
  design-01's estimate exactly.
- `Workflow.steps()` (`models.py:157-166`) returns a de-duplicated tuple already
  excluding `END` — confirms the union-by-dict-comprehension approach in
  design-01's `step_queues` construction is sound: iterating
  `for workflow in resolved.values() for step in workflow.steps()` naturally
  de-dupes on the dict key with no extra set logic needed.

Nothing in the design conflicts with the numbered invariants in this repo's
`CLAUDE.md`. Specifically: #4 (`route()` stays pure — untouched, still takes a
single `Workflow`), #5/#11/#17 (dispatcher/consumer/projection/api stay blind
to drivers — respected, `ServedWorkflowRepository` is a driver reached only
from `app.py`), #7 (event carries `task`+`queue` — untouched, this change only
touches the `started` event's own payload, not `dispatched`/`finished`/`failed`).

## Proposed architecture

Ratifying design-01's shape: **the served set is a value, decided once, at
the edge.**

```
cli.py            resolves CLI flags → tuple[str, ...] of served names
                   (the ONLY place that interprets --workflow/--all-workflows)
        │
        ▼
app.build()        str | Sequence[str] → normalize → resolve each via the
                   RAW repository (fail fast) → union step_queues → wrap the
                   repository (ServedWorkflowRepository) → hand the WRAPPED
                   one to Dispatcher, the RESOLVED Workflow objects to
                   BoardProjection
        │
        ▼
Harness            workflows: dict[str, Workflow], fixed for process lifetime
```

Two decisions worth stating explicitly as *architecture*, not just
implementation detail, because they're the two places a less careful
implementation could quietly violate an invariant:

1. **`Dispatcher` must never see the raw repository.** It has to see the
   `ServedWorkflowRepository`-wrapped one, or FR-5 (fail-fast for a valid-but-
   unserved workflow) silently doesn't hold — the raw repository will happily
   `.get()` any file on disk. This is a single wiring line in `app.py`; get it
   wrong and the regression is invisible until a task for an unserved
   workflow reaches a step with no queue and produces the confusing message
   FR-5 exists to eliminate. Flagging as a review checkpoint, not a code
   change — design-01 already sequences this correctly (§"`build()` always
   resolves ... through the raw ... repository first").
2. **Resolution order = served order, and it flows through unchanged from CLI
   to `BoardProjection`.** `--workflow default --workflow hotfix` must produce
   `["default", "hotfix"]` in that order at every layer — `cli.py`'s parsed
   name list, `app.build()`'s `resolved` dict (insertion-ordered, Python
   dict semantics guarantee this), and `BoardProjection`'s column-union loop.
   No layer is allowed to re-sort except `--all-workflows`, which is
   permitted (indeed required, for determinism) to use `names()`'s sorted
   output. Don't let a `set()` or an unordered dict merge slip into any of
   these three hops — that's the one way this feature could regress board
   column ordering for existing single-workflow deployments, which is the
   one behavior FR-2 explicitly protects.

### Refinements to design-01 (small, worth doing now rather than in review)

**R1 — De-duplicate the served-name list before it reaches
`ServedWorkflowRepository`.** `harness run --workflow default --workflow
default` (a plausible typo, e.g. from a wrapper script building the flag list
programmatically) should not produce a served-list error message that lists
`default` twice (`served: default, default`). `resolved = {name: repo.get(name)
for name in names}` in `app.build()` already de-dupes correctly for the
`workflows` dict (dict keys are unique), but `ServedWorkflowRepository._served
= tuple(names)` as design-01 writes it does not — it stores the raw,
possibly-duplicated tuple, which only shows up in the error message's `served:
...` list, not in a correctness bug. Cheap fix: build
`ServedWorkflowRepository` from `tuple(resolved)` (the already-deduped dict's
keys) rather than from `names` directly. One line, no new test needed beyond
what FR-6 already asks for on the message shape.

**R2 — `--all-workflows` with zero definitions found is a startup error, not
an empty served harness.** Design-01 already says this in its
`_resolve_served_workflows` sketch (`if not names: error: no workflow
definitions found`) — I'm promoting it from a sketch detail to an explicit
requirement here because a harness with zero step queues and zero consumers
would start, log `started`, and then sit there doing nothing with no
diagnostic — worse than refusing to start. Same `return 2` pattern as every
other `_run` startup check.

**R3 — `--github-workflow` validation (design-01's "add it" call): agree, and
scope it precisely.** Reject at `_run` startup when
`args.github_workflow not in served_names`, using the *same* resolved name
list `_resolve_served_workflows` already produced — don't re-derive it via a
second repository read. This is genuinely in-scope (it's the same "resolve
the served set, fail fast on a mismatch" work, applied to one more input) and
cheap because the served-name list already exists in hand at that point in
`_run` (before `sources = _github_sources(...)` at `cli.py:667`, per
design-01's placement). Don't expand this into validating `--github-label` or
other GitHub-source options — those aren't workflow names and are out of this
task's scope.

## Implementation guidance

Build in this order — each step is independently testable and later steps
depend on earlier ones, so this order minimizes rework and keeps the suite
green between commits:

1. **`ports/workflows.py`**: add `WorkflowRepository.names() -> tuple[str,
   ...]` as an abstract method. This alone will not break anything at import
   time (Python ABCs only enforce at instantiation), but do it first so step 2
   can implement against a real port contract.
2. **`drivers/fs_workflows.py`**: implement `FilesystemWorkflowRepository.names()`
   and add `ServedWorkflowRepository` (apply R1: dedupe the served tuple from
   whatever's handed to it, e.g. `tuple(dict.fromkeys(names))`). Unit-test both
   in isolation — no `app.py` involvement yet. This is the only new production
   class in the whole change; keep it in this file (no new module) per
   design-01's reasoning (~15 lines, one caller).
3. **`projection.py`**: extract the existing BFS body of `column_order` into a
   `_reachable_order(workflow)` helper, change `column_order`/`BoardProjection.__init__`
   to take `Sequence[Workflow]`. Update all ~23 test call sites
   (`BoardProjection(wf)` → `BoardProjection([wf])`) in the same commit —
   this is mechanical and is the regression safety net for FR-2, so it must
   land before or with the `app.py` change, not after.
4. **`app.py`**: widen `build()`'s second parameter, normalize to a tuple,
   resolve via the raw repository, build the union `step_queues`, wrap in
   `ServedWorkflowRepository` (apply R1 here: pass `tuple(resolved)`, not
   `names`), rename `Harness.workflow` → `Harness.workflows: dict[str,
   Workflow]`, update the `started` event to `workflows=sorted(self.workflows)`.
   Fix the one production reader inside `app.py` itself (none currently exist
   outside `Harness.run()`'s `started` emit — confirmed above).
5. **`cli.py`**: make `--workflow` repeatable (`action="append"`), add
   `--all-workflows`, write `_resolve_served_workflows` (manual validation,
   not `argparse` mutual-exclusion, per the alignment note above; apply R2),
   fix `_init`'s two `harness.workflow` reads (`cli.py:103,107`) to
   `harness.workflows[args.workflow]`, and add the `--github-workflow`
   membership check from R3 in `_run`.
6. **Tests (FR-6)**: two workflows served together including a shared step
   name; unserved-but-existing workflow fails per FR-5's message shape;
   genuinely nonexistent workflow still fails as today; single-workflow runs
   unchanged (regression); `BoardProjection` column union with no duplicates;
   `WorkflowRepository.names()` on the filesystem driver;
   `ServedWorkflowRepository` unit tests including the R1 dedup case and the
   R2 empty-`--all-workflows` case.
7. `.venv/bin/pytest -q` — full suite green, including
   `tests/test_architecture.py` (the structural guard for invariants #1, #2,
   #5, #11, #17 above) and `tests/test_smoke.py`/`test_smoke_git.py` (real-FS
   coverage; neither should need a behavior change, only the mechanical
   `BoardProjection([...])` call-site fix if they construct one directly —
   check before assuming no change needed there).

### Key interfaces (unchanged from design-01, restated for implementers)

```python
# ports/workflows.py
class WorkflowRepository(ABC):
    def get(self, name: str) -> Workflow: ...          # unchanged
    def names(self) -> tuple[str, ...]: ...             # NEW

# drivers/fs_workflows.py
class ServedWorkflowRepository(WorkflowRepository):
    def __init__(self, inner: WorkflowRepository, names: Sequence[str]) -> None: ...
    def get(self, name: str) -> Workflow: ...            # WorkflowNotFound if unserved
    def names(self) -> tuple[str, ...]: ...

# app.py
def build(root: Path, workflows: str | Sequence[str], *, ...) -> Harness: ...
class Harness:
    workflows: dict[str, Workflow]                       # was: workflow: Workflow

# projection.py
class BoardProjection(BoardView):
    def __init__(self, workflows: Sequence[Workflow]) -> None: ...
```

### Data flow for the new failure path (FR-5)

```
task.workflow_template = "other"  (valid file on disk, not served)
        │
Dispatcher.tick()
        │  self._workflows.get("other")   # self._workflows is ServedWorkflowRepository
        ▼
ServedWorkflowRepository.get("other")
        │  "other" not in self._served
        ▼
raise WorkflowNotFound("workflow 'other' is not served by this harness (served: default, hotfix)")
        │
Dispatcher.tick() except-clause (dispatcher.py:61-63, UNCHANGED)
        ▼
self._fail(task, str(error))  →  task moves to failed/ with that reason
```

No new exception type, no new dispatcher branch — the entire feature's
failure path rides the exact except-clause that exists today for a genuinely
missing workflow file. That's the strongest evidence this is scoped
correctly: FR-5 is implemented entirely below the dispatcher, in a decorator
the dispatcher can't tell apart from a plain repository.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `Dispatcher` accidentally wired to the raw repository instead of the `ServedWorkflowRepository`-wrapped one, silently reopening the FR-5 gap. | Single wiring line in `app.py`; add a test that a *valid but unserved* workflow fails with the new message (not the generic "no queue" one) — this test only passes if the wrapped repo is actually wired in. |
| Column order for existing single-workflow deployments shifts because a merge step in `BoardProjection`/`app.py` re-sorts instead of preserving insertion order. | FR-2's regression tests (existing `BoardProjection([workflow])` call sites) catch any visible column reordering; also covered by the "resolution order = served order" rule stated above — implementers should not introduce a `set()` anywhere in this path. |
| `harness init`/`harness submit` accidentally coupled to the multi-workflow surface, breaking their single-name contract. | Both are explicitly out of scope (plan-01, design-01, this doc) — `build(root, args.workflow)` keeps passing a bare `str`, hitting the unchanged `isinstance` branch. No code change needed in either subcommand beyond `_init`'s two `.workflow` → `.workflows[name]` reads. |
| A step name shared by two served workflows silently merges behavior/agent persona, surprising an operator who intended them to differ. | Not a bug — FR-3's explicit, already-existing codebase behavior (`behavior_for`/`AgentCatalog.get` are keyed by step name alone today, for a single workflow too). Document it in the `--all-workflows`/`--workflow` CLI help text so it's discoverable without reading the source; a `(workflow, step)`-keyed alternative is a materially larger change (agent catalog + behavior lookup both need a new key) and not warranted by this task's scope. |
| `--all-workflows` on a directory with zero definitions starts a harness that does nothing. | R2 above — treat as a startup error, `return 2`, before `build()` is even called. |
| Interaction with the unmerged `maxParallel` branch (`tsk_2fb79172220a45f3`) if it lands first or concurrently. | Out of scope per plan-01; this design introduces no per-step cardinality logic, so there is nothing here to reconcile until that branch actually merges. If/when it does, the reconciliation is additive (a conflict rule for a shared step's differing `maxParallel`), not a rework of this design. |

## Prerequisites before implementation begins

None blocking. Specifically confirmed:
- The `maxParallel` branch this plan flags as a dependency-risk is **not** on
  `main`/this branch today (checked: no `max_parallel_for` in `models.py`, no
  `maxParallel` validation in `fs_workflows.py`) — safe to proceed without
  waiting on it, per plan-01's own scoping.
- No other in-flight work touches `app.build()`, `projection.py`,
  `ports/workflows.py`, `drivers/fs_workflows.py`, or the `run` subcommand of
  `cli.py` (checked current diff/log — this branch's history is only the
  plan/design artifacts for this task).
- `tests/test_architecture.py` already exists and will exercise the relevant
  invariants without any change to that file itself — no test-infrastructure
  prerequisite to add first.

Implementation can start directly from step 1 of the ordered plan above.
