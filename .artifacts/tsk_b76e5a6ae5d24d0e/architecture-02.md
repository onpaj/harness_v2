# Architecture (revision 2): Make workflows optional

## Verdict on the design

I re-read `design-02.md` and `plan-02.md` against the current source
(`models.py`, `router.py`, `dispatcher.py`, `app.py`, `projection.py`,
`cli.py`, `ports/{workflows,agent,board}.py`, `drivers/{fs_workflows,
fs_agents,memory,github_source}.py`, `api/templates/_task.html`,
`test_architecture.py`). Every quoted snippet and line reference in
design-02 checks out byte for byte against what's on disk today — the two
gaps architecture-01 raised (the `MemoryAgentCatalog`/`MemoryWorkflowRepository`
`.names()` blocker, the `_task.html` literal-`None` row) are now correctly
folded in as FR-9 and the sequencing NFR, and design-02 additionally
resolved them with concrete, correct code. I'm endorsing design-02 as the
implementation contract, **with one required correction** below: the new
`invalid_step_name` reserved-name guard is incomplete in a way that
reintroduces a real board-rendering bug through the exact door this task
opens, and one smaller inconsistency in how `run`'s `--workflow` default is
handled compared to its two siblings (`submit`, `_github_sources`).

## Alignment with existing patterns

Confirmed unchanged from architecture-01's assessment: the `Workflow | None`
split keeps `route()` a pure function per invariant 4 (no new import lands in
`router.py` — `test_router_only_knows_models`, `test_architecture.py:25`,
keeps passing unmodified), `.names()` mirrors `RepositoryRegistry.names()`'s
existing shape (`drivers/memory.py:372-373`, confirmed identical pattern:
`return list(self._repos)`), and `consumer.py` needs zero changes (grep
confirms `workflow_template` is touched only by `models.py`, `cli.py`,
`dispatcher.py`, `drivers/{memory,github_source}.py`, and the two templates —
no fifth consumer of the field exists).

## Proposed architecture

No new components, unchanged from architecture-01's assessment: this is a
schema-and-routing generalization layered onto the existing five-layer module
map. FR-7's union rule (`WorkflowRepository.names()` ∪ `AgentCatalog.names()`)
remains the right call and I have nothing new to add there — design-02's
sequencing note (land `.names()` and all four concrete implementations in the
same commit as the abstract method) is the correct, and now explicit,
prerequisite.

## Implementation guidance

### 1. Required correction: `invalid_step_name`'s reserved-name set is incomplete

Design-02 (`design-02.md`, `router.py` §, row 3 of the truth table, and the
`invalid_step_name` sketch in the `ports/workflows.py`/`drivers/fs_workflows.py`
§) only reserves `END` ("end") and `FAILED` ("failed") — both defined in
`models.py`. It misses that the board has a **third** reserved name that
lives outside `models.py` entirely: `DONE_COLUMN = "done"`
(`ports/board.py:19`, confirmed), alongside `TODO_COLUMN = "todo"`
(`ports/board.py:16`).

Here is the concrete failure this produces. Take a `--no-workflow` harness
with `agents/done.json` present (an operator naming a "wraps things up"
agent `done` is a plausible mistake, not a contrived one — nothing in FR-7's
union rule or `FilesystemAgentCatalog` rejects the name). FR-7's discovery
puts `"done"` into `known_steps` via `AgentCatalog.names()`. A task submitted
with `--step done` passes `invalid_step_name` cleanly (it isn't `end` or
`failed`), gets a real queue at `queues/done/` (distinct on disk from
`layout.done` at `<root>/done/`, so no filesystem collision), and reaches the
board under column `"done"`. Once the agent step finishes, FR-2 routes it to
`Finished()` — the dispatcher's `_finish()` (`dispatcher.py:99-111`,
unchanged) moves it into the *real* `done/` queue and emits `queue="done"`.

Now look at `column_order()` as design-02 rewrites it
(`design-02.md`'s `projection.py` §): the reachability walk / fallback loop
places `"done"` into `order` (it's a known step), and the function
unconditionally returns `(TODO_COLUMN,) + tuple(order) + (DONE_COLUMN,
FAILED_COLUMN)` — appending the literal string `"done"` a **second** time.
`BoardProjection.snapshot()` (`projection.py:89-103`, untouched by design-02)
iterates `for name in self._order` and builds one `BoardColumn` per entry,
filtering `self._columns[task_id] == name`. With `"done"` appearing twice in
`self._order`, the board renders **two side-by-side columns both titled
"done"**, and every task whose stored column is `"done"` — which now includes
both the mid-pipeline `done`-named step *and* every genuinely finished task —
appears, duplicated, in both. Same failure mode for a step named `"todo"`.

This exact class of bug is **already latent today** — nothing before this
task prevented a hand-authored `workflows/*.json` from naming a step
`"done"`/`"todo"`/`"failed"` (`Workflow.steps()` only special-cases `END`,
confirmed at `models.py:157-166`). But today it requires an operator to
deliberately hand-edit a workflow JSON file with a nonsensical step name —
rare, deliberate, low blast radius. This task lowers the bar to a single
plausible CLI typo (`harness submit --step done`) or an innocuously-named
agent file, which is precisely the risk design-02 already identifies as the
reason `invalid_step_name` needs to exist at all ("a step name previously
only ever came from inside a trusted workflow file... `Task.step` changes
that" — design-02.md, `router.py` §). The same reasoning that justifies
reserving `end`/`failed` justifies reserving `done`/`todo` — the guard is
just missing two of the four reserved board-level names.

**Fix, same shape design-02 already proposed, extended by one import:**

```python
# drivers/fs_workflows.py
from harness.models import END, FAILED
from harness.ports.board import DONE_COLUMN, TODO_COLUMN

_RESERVED_STEP_NAMES = (END, FAILED, DONE_COLUMN, TODO_COLUMN)

def invalid_step_name(name: str) -> bool:
    return invalid_workflow_name(name) or name in _RESERVED_STEP_NAMES
```

This is a driver, not `router.py` — importing `ports/board.py` here is fine
and adds no new dependency edge that `test_architecture.py` polices (only
`router.py`/`dispatcher.py`/`consumer.py` are restricted to
models/ports-only import sets; `fs_workflows.py` already imports freely
within `ports/`). **Router row 3's own defensive check stays exactly as
design-02 wrote it** (`task.step in (None, END, FAILED)`) — it must not grow
a `ports/board` import, since `router.py` is restricted to `models` alone
(invariant 4, `test_router_only_knows_models`). The CLI/`TaskSource` boundary
check is the correct — and only necessary — place for the full four-name
reservation; the router's belt-and-suspenders check only ever needs the two
names `models.py` itself knows about, which is what design-02 already has
right for that half.

### 2. Inconsistency: `run`'s `--workflow` default silently changes today's default invocation

Design-02 changes `run.add_argument("--workflow", default=DEFAULT_WORKFLOW)`
(confirmed current, `cli.py:757`) to `default=None`, then asserts: "A harness
initialized the ordinary way (`workflows/default.json` present) behaves
identically whether or not `--workflow` is passed." That claim does not
hold for the single most common invocation — **plain `harness run`, no
flag at all** — which is exactly the case whose default value this change
touches.

Before this task: omit `--workflow` → `args.workflow == "default"` →
`build(root, "default", ...)` → `Harness.workflow` is the real `Workflow`
object → the `"started"` event carries `workflow="default"`
(`app.py:166`, confirmed unchanged by design-02). After this task, with the
default flipped to `None`: omit `--workflow` → `args.workflow is None` →
`Harness.workflow is None` → the `"started"` event now carries
`workflow=None`. Every existing deployment that runs `harness run` without
explicitly typing `--workflow default` (i.e. nearly everyone, since that's
the point of it being a default) sees this field flip on upgrade, with no
change to their setup. `Harness.workflow` has exactly one other reader
(`cli.py:103,107`, inside `_init`, which is unaffected since `_init`'s own
`--workflow` default is correctly left unchanged) — so the blast radius is
confined to this one telemetry field, but it's an unannounced regression in
an operational signal on a harness that (per `CLAUDE.md`) runs unattended
under launchd and is debugged partly through its event stream.

Design-02 already solved the structurally identical problem correctly, twice,
elsewhere in the same document — `_submit` and `_github_sources` both flip
their argparse default to `None` *and then restore today's effective default*
with a one-line fallback (`design-02.md`: "`if workflow_name is None and step
is None: workflow_name = DEFAULT_WORKFLOW   # unchanged default`", and the
identical line in `_github_sources`). `run`'s plain `--workflow` — which has
no sibling `--step` flag to be mutually exclusive with, and so needed no
argparse-level reason to become `None` in the first place — is the one place
this pattern wasn't carried over, purely because there's no mutual-exclusion
group forcing the question. But the underlying need (support a
`--no-workflow`-initialized harness, where `workflows/default.json` doesn't
exist, so `build(root, "default", ...)` would raise `WorkflowNotFound`) is
exactly the same shape.

**Fix:** keep `run`'s `--workflow` default as `None` at the argparse level
(needed so an omitted flag can mean "no workflow" for a `--no-workflow`
harness), but resolve the *effective* default the same way `_submit` and
`_github_sources` already do — probe for the conventional file rather than
assume its absence:

```python
def _run(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    workflow_name = args.workflow
    if workflow_name is None and (layout.workflows / f"{DEFAULT_WORKFLOW}.json").is_file():
        workflow_name = DEFAULT_WORKFLOW   # unchanged default when one exists
    ...
    harness = build(root, workflow_name, ...)
```

This makes all three CLI entry points (`submit`, `run`, `_github_sources`)
follow one consistent rule — *explicit request wins, otherwise fall back to
today's conventional default if it's actually there, otherwise `None`* —
instead of `run` being the only one that turns "the operator didn't type
anything" into a silent behavior change. An explicit `--workflow typo` still
fails loudly via `WorkflowNotFound` exactly as today, since the fallback only
fires when the flag is entirely absent.

### 3. Everything else: build to design-02 as written

Every other component section — `models.py`, the 8-row router truth table
(rows 1/2/4/5/6/7/8; row 3 unchanged from the correction above),
`dispatcher.py`, `app.py`, `projection.py`'s `column_order`/`BoardProjection`
signature change (independent of the reserved-name fix — the function itself
is correctly written, it just needs callers to never hand it a reserved
name), `cli.py`'s `submit`/`init` flags, `drivers/github_source.py`, and
`api/templates/_task.html` — is confirmed accurate against current source
and requires no further correction. In particular:

- `MemoryWorkflowRepository`/`MemoryAgentCatalog` genuinely lack `.names()`
  today (confirmed at `drivers/memory.py:67-75` and `297-307`) — design-02's
  sequencing requirement (land the abstract method and all four concrete
  implementations in one commit) is correctly binding, not optional.
- `ports/workflows.py`/`ports/agent.py` today have only `get()` as an
  abstract method (confirmed) — the new `names()` abstract method is a clean
  addition, no existing subclass beyond the four design-02 names exists
  (grep: exactly `FilesystemWorkflowRepository`/`MemoryWorkflowRepository` and
  `FilesystemAgentCatalog`/`MemoryAgentCatalog`).
- `_task.html:10`'s `workflow` row genuinely has no `{{ x or "—" }}` fallback
  today (confirmed) — FR-9's fix is correctly scoped.
- `app.py`'s `behavior_for(step)` (`app.py:283-296`, unchanged by design-02)
  correctly keeps working under the union rule: a standalone agent step not
  in any workflow still resolves through `catalog.get(step)` when a catalog
  is wired, and still falls back to the shared `DummyBehavior` otherwise —
  no change needed there, confirmed by re-reading it against FR-7.

### Data flow

Unchanged from architecture-01's diagram — the only new edge is
`workflow=None` flowing into `route()`. The correction in §1 sits entirely at
the CLI/`TaskSource` boundary (`invalid_step_name`) and doesn't change this
shape.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `--step done`/`--step todo` (or an `agents/done.json`) silently duplicates a board column and double-renders every task that reaches it | Extend `invalid_step_name` to reserve `DONE_COLUMN`/`TODO_COLUMN` from `ports/board.py`, not just `END`/`FAILED` from `models.py` (§1) — this is a required correction to design-02, not a nice-to-have |
| `harness run` (no flags) silently changes the `"started"` event's `workflow` field from `"default"` to `None` on every existing deployment | Resolve `run`'s effective workflow name by probing for `workflows/default.json` the same way `_submit`/`_github_sources` already restore their default, instead of unconditionally defaulting to `None` (§2) |
| `MemoryAgentCatalog`/`MemoryWorkflowRepository` missing `.names()` breaks every test that builds one the instant the abstract method lands | Unchanged from architecture-01: land in the same commit as the abstract method (design-02 already specifies this correctly as a binding sequencing step) |
| Board renders `None` literally for a workflow-less task's detail page | Unchanged from architecture-01: FR-9's template fix, already correctly specified in design-02 |
| A workflow-less task with a hand-corrupted `status` silently finishes instead of failing (no membership set to check against) | Already flagged and accepted by the plan — no action needed |

## Prerequisites before implementation begins

1. Same as architecture-01: `.names()` on `MemoryAgentCatalog` and
   `MemoryWorkflowRepository` must land in the same commit as the abstract
   methods on `WorkflowRepository`/`AgentCatalog` — hard ordering dependency.
2. **New:** `invalid_step_name` must reserve all four board-level names
   (`end`, `failed`, `done`, `todo`), not two — this must be correct in the
   same commit that introduces the reserved-name guard (sequencing step 1 in
   design-02's list), since it's the first commit where `--step` becomes
   reachable at all.
3. **New:** `_run`'s workflow-name resolution must use the
   probe-for-the-conventional-file pattern (§2), not a bare `default=None`,
   before `cli.py`'s `run` flags are wired — otherwise the very first test
   asserting "plain `harness run` behaves identically to before" (implied by
   FR-5/FR-6's acceptance criteria) will fail on the `"started"` event's
   `workflow` field.
4. Everything else in design-02's 10-step sequencing order stands unchanged.
