# Design (revision 2): Make workflows optional

This supersedes `design-01.md` as the implementation contract. It folds in
`plan-02.md`'s finalized FRs (FR-9, the reserved-step-name guard, the
`.names()`-first sequencing) and the two architecture gaps that weren't yet
design decisions when `design-01.md` was written. Every snippet below was
checked against the current source (`models.py`, `router.py`,
`dispatcher.py`, `app.py`, `projection.py`, `cli.py`, `ports/{workflows,
agent}.py`, `drivers/{fs_workflows,fs_agents,memory,github_source}.py`,
`api/templates/_task.html`) as of this writing — line numbers below are
current, not aspirational.

This feature introduces no new user interface — it touches the CLI (two new
flags, one new mutually-exclusive pair) and one existing board template (a
nullable-field fallback plus one new row), with no new interaction model. Per
scope, the UX/UI section is omitted; the template fix is covered under
Component design instead.

## Guiding decisions (carried over from design-01, unchanged)

**Queue discovery (FR-7): union, not agent-catalog-only.** The live
step-queue set is the union of two enumerations, each a new `.names()` method
on an existing repository port:

- every step reachable in every workflow file under `workflows/`
  (`WorkflowRepository.names()` → `.get(name).steps()` for each, skipping a
  file that fails to parse), and
- every agent declared under `agents/` (`AgentCatalog.names()`), when a
  catalog is wired in.

With exactly one workflow file and no catalog (today's shape for every
in-memory test and the `--agent dummy` path), the union degenerates to
exactly what `build()` computes today — a strict backward-compatible
generalization, confirmed against `app.py:233-258`.

**Entry step is a second field, not an overload.** `Task.step: str | None`
carries the workflow-less entry queue. This keeps `route()` a pure function
of `(Task, Workflow | None)` with no repository lookup inside it (invariant
4) — the two-field design was chosen specifically so the router never needs
to ask "is this name a workflow or a step," the caller (dispatcher) already
resolved that before calling `route()`.

## Component design

### `models.py`

`Task` (currently `models.py:78-99`, field order `id, workflow_template,
created, repository, worktree, status, last_outcome, lock_id, dedup_key,
history, data`) changes to:

```python
@dataclass(frozen=True)
class Task:
    id: str
    created: str
    workflow_template: str | None = None   # was: required str, 3rd field
    step: str | None = None                # NEW: entry step for a workflow-less task
    repository: str | None = None
    worktree: str | None = None
    status: str | None = None
    last_outcome: str | None = None
    lock_id: str | None = None
    dedup_key: str | None = None
    history: tuple[HistoryEntry, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
```

`id`/`created` move ahead of `workflow_template` because Python requires
every non-default field to precede every default field, and
`workflow_template` is gaining one. Confirmed safe: the only three
construction sites in the repo (`cli.py:324`, `drivers/memory.py:238`,
`drivers/github_source.py:79`) all use keyword arguments exclusively — grep
found no positional `Task(...)` call anywhere.

`to_dict` (`models.py:101-114`) adds `"step": self.step`. `from_dict`
(`models.py:117-132`) changes `workflow_template=raw["workflowTemplate"]` to
`raw.get("workflowTemplate")` (was a required key; a file written before this
change always has the key, so this is a pure widening) and adds
`step=raw.get("step")` (absent on every pre-existing file → `None`, exactly
the workflow-carrying default).

`Workflow`, `Transition`, `Workflow.steps()`/`.target()` (`models.py:135-166`)
are unchanged. `Workflow.steps()` remains the membership set FR-4's
known-vs-unknown check is judged against.

### `router.py`

Signature changes from `route(task: Task, workflow: Workflow) -> Decision`
(`router.py:8`) to `route(task: Task, workflow: Workflow | None) -> Decision`.
Full replacement for the current four-branch body (`router.py:14-32`):

| # | `task.status` | `workflow` | `last_outcome` | `task.step` | Decision |
|---|---|---|---|---|---|
| 1 | `None` | given | – | – | `MoveTo(workflow.start)` (unchanged) |
| 2 | `None` | `None` | – | valid, not reserved | `MoveTo(task.step)` |
| 3 | `None` | `None` | – | `None` or in `(END, FAILED)` | `Failed("workflow-less task has no usable step")` |
| 4 | set | any | `None` | – | `Failed("task has status {status!r} but no lastOutcome")` (unchanged) |
| 5 | set | `None` | set | – | `Finished()` — always (FR-2) |
| 6 | set | given | set | – | `status` unknown to `workflow.steps()` → `Failed("workflow {name!r} has no step {status!r}")` (FR-4, preserved) |
| 7 | set | given | set | – | `status` known, no edge for `(status, outcome)` → `Finished()` (FR-3 — changed from `Failed`) |
| 8 | set | given | set | – | edge target is `END` → `Finished()` (unchanged); target is another step → `MoveTo(target)` (unchanged) |

Row 3 is the reserved-step-name guard added since `design-01.md` (FR-4 in
`plan-02.md`). It only needs `END`/`FAILED` from `models.py` — no import of
`drivers.fs_workflows.invalid_step_name` is needed or allowed here, since
`router.py` must import only `models` (guarded today by
`test_router_only_knows_models`, `test_architecture.py:22`). The
path-traversal/empty-string shape check (`invalid_workflow_name`'s other
half) is a CLI/`TaskSource`-boundary concern (§ below), not the router's —
the router only ever sees a `Task` that has already passed that boundary, so
its own defense is narrowly the two reserved terminals it directly knows
about via `models.py`.

Implementation sketch:

```python
def route(task: Task, workflow: Workflow | None) -> Decision:
    if task.status is None:
        if workflow is not None:
            return MoveTo(workflow.start)
        if task.step is None or task.step in (END, FAILED):
            return Failed("workflow-less task has no usable step")
        return MoveTo(task.step)

    if task.last_outcome is None:
        return Failed(f"task has status {task.status!r} but no lastOutcome")

    if workflow is None:
        return Finished()

    if task.status not in workflow.steps():
        return Failed(f"workflow {workflow.name!r} has no step {task.status!r}")

    target = workflow.target(task.status, task.last_outcome)
    if target is None:
        return Finished()
    if target == END:
        return Finished()
    return MoveTo(target)
```

This is a strict superset of the current body: with `workflow` always given
and `task.step` always `None` (today's only possible input shape), rows
1/4/6/8 are exactly today's four branches, byte for byte.

### `ports/workflows.py` / `drivers/fs_workflows.py`

New abstract method, added in the same commit as every concrete
implementation (see Sequencing below):

```python
class WorkflowRepository(ABC):
    @abstractmethod
    def get(self, name: str) -> Workflow: ...

    @abstractmethod
    def names(self) -> tuple[str, ...]:
        """Every workflow name discoverable without raising. Best-effort:
        a name whose file fails to parse is simply absent from the result."""
```

`FilesystemWorkflowRepository.names()` (new method alongside `get()`,
`fs_workflows.py:21-62`): globs `<root>/*.json`, keeps stems that pass
`not invalid_workflow_name(stem)`, sorted.

`invalid_step_name`, next to the existing `invalid_workflow_name`
(`fs_workflows.py:12-18`, unchanged) — same module because it composes that
shape check with the two reserved terminals from `models.py`:

```python
from harness.models import END, FAILED

def invalid_step_name(name: str) -> bool:
    """Everything invalid_workflow_name rejects, plus the reserved
    terminal names `end`/`failed` — a step name now comes straight from
    `--step`/a TaskSource, not only from inside a trusted workflow file."""
    return invalid_workflow_name(name) or name in (END, FAILED)
```

`cli.py` already imports `invalid_workflow_name` from this module
(`cli.py:23`); it imports `invalid_step_name` the same way.

### `ports/agent.py` / `drivers/fs_agents.py`

Same shape, symmetric to workflows:

```python
class AgentCatalog(ABC):
    @abstractmethod
    def get(self, name: str) -> AgentSpec: ...

    @abstractmethod
    def names(self) -> tuple[str, ...]:
        """Every agent name discoverable without raising."""
```

`FilesystemAgentCatalog.names()` (new, alongside `get()`,
`fs_agents.py:32-75`): globs `<root>/*.json`, filters
`not _invalid_agent_name(stem)`, sorted.

### `drivers/memory.py` — sequencing-critical

Confirmed against current source: **`MemoryWorkflowRepository`
(`memory.py:67-75`) and `MemoryAgentCatalog` (`memory.py:297-307`) each
implement only `get()` today — neither has `.names()`.** The moment
`WorkflowRepository.names()`/`AgentCatalog.names()` become abstract, every
existing call site that constructs one of these (which includes every phase-3
agent-catalog test) raises `TypeError: Can't instantiate abstract class`.
This is the hard blocker architecture-01 flagged; it is now a first-class
requirement (`plan-02.md`'s NFR), not an afterthought:

```python
class MemoryWorkflowRepository(WorkflowRepository):
    ...
    def names(self) -> list[str]:
        return list(self._workflows)


class MemoryAgentCatalog(AgentCatalog):
    ...
    def names(self) -> list[str]:
        return list(self._specs)
```

Both must land in the **same commit** as the new abstract methods — never as
a follow-up. (`MemoryRepositoryRegistry.names()` at `memory.py:372-373`
already has exactly this shape for a third port; these two mirror it.)

Grep confirms exactly three implementers of each port (filesystem, memory,
the ABC) — no fourth subclass needs updating.

### `dispatcher.py`

One conditional added in `tick()` (currently `dispatcher.py:59-65`):

```python
try:
    workflow = (
        self._workflows.get(task.workflow_template)
        if task.workflow_template is not None
        else None
    )
except WorkflowNotFound as error:
    self._fail(task, str(error))
    return True

decision = route(task, workflow)
```

Everything downstream (`_move`/`_finish`/`_fail` at `dispatcher.py:80-133`,
event emission, history) is untouched — the dispatcher still owns "where
next" exclusively (invariant 3); it just now sometimes resolves `workflow` to
`None` before calling `route()`.

### `app.py`

`build()`'s `workflow_name: str` (`app.py:202`) becomes
`workflow_name: str | None = None`. Current queue construction
(`app.py:233-259`, `workflow = workflows.get(workflow_name)` then
`step_queues = {step: ... for step in workflow.steps()}`) becomes:

```python
workflows = FilesystemWorkflowRepository(layout.workflows)
workflow: Workflow | None = None
if workflow_name is not None:
    workflow = workflows.get(workflow_name)   # WorkflowNotFound still propagates, fail fast

known_steps: set[str] = set()
for name in workflows.names():
    try:
        known_steps |= set(workflows.get(name).steps())
    except WorkflowNotFound:
        continue   # unreadable file during discovery: skipped, not fatal
if catalog is not None:
    known_steps |= set(catalog.names())

steps = tuple(sorted(known_steps))
step_queues = {
    step: FilesystemTaskQueue(name=step, root=layout.queues / step, events=events, quarantine=failed)
    for step in steps
}
```

`BoardProjection(workflow)` (`app.py:236`) becomes
`BoardProjection(steps=steps, workflows=[workflows.get(n) for n in workflows.names() if <loadable>])`
— every loadable workflow feeds ordering, never just the one named at
`build()` time.

`Harness.workflow` (`app.py:90, 106`) becomes `Workflow | None`; the
`"started"` event (`app.py:166`, `workflow=self.workflow.name`) becomes
`workflow=self.workflow.name if self.workflow else None`.

Nothing else in `Harness` changes: `recover()` (`app.py:121-126`),
`_seed_pollers()` (`app.py:128-145`), and the three loops in `run()`
(`app.py:174-197`) already work off `self._step_queues` (a plain dict),
confirmed never off `self.workflow`.

### `projection.py`

`column_order` (currently `projection.py:25-47`, takes one `Workflow`) and
`BoardProjection.__init__` (`projection.py:51-52`, takes one `Workflow`)
change to:

```python
def column_order(steps: Iterable[str], workflows: Iterable[Workflow] = ()) -> tuple[str, ...]:
    """Steps ordered by reachability from every workflow's start (walked in
    the order the workflows are given), then every remaining known step
    (workflow-less, or unreferenced by any workflow) in the order `steps`
    was given, then done and failed."""
    order: list[str] = []
    known = set(steps)
    pending = [w.start for w in workflows]
    edges = [t for w in workflows for t in w.transitions]

    while pending:
        step = pending.pop(0)
        if step == END or step in order or step not in known:
            continue
        order.append(step)
        for t in edges:
            if t.from_step == step and t.to_step not in order:
                pending.append(t.to_step)

    for step in steps:
        if step not in order:
            order.append(step)

    return (TODO_COLUMN,) + tuple(order) + (DONE_COLUMN, FAILED_COLUMN)


class BoardProjection(BoardView):
    def __init__(self, steps: Iterable[str], workflows: Iterable[Workflow] = ()) -> None:
        self._order = column_order(steps, workflows)
        ...  # rest of __init__ unchanged
```

`hydrate()`/`apply()`/`snapshot()`/`_store()`/`_bump()`
(`projection.py:58-141`) need no change — confirmed they only ever read
`self._order` and per-task columns, never a `Workflow` object directly.

**FR-8/FR-9 check:** a workflow-less task submitted with `--step development`
produces `steps=(..., "development", ...)`. If no workflow's walk reaches
`"development"` it lands in `order` via the fallback loop, giving exactly
`todo → development → done` on the board — while a pipeline task's columns
are unaffected because its steps are still walked in workflow order first,
same as `column_order` does today with a single workflow.

### `cli.py`

**`submit`** (`cli.py:747-753`, `_submit` at `cli.py:311-336`) — mutually
exclusive `--workflow`/`--step` (a new argparse pattern in this file; no
existing subcommand uses `add_mutually_exclusive_group()`, confirmed by
grep):

```python
group = submit.add_mutually_exclusive_group()
group.add_argument("--workflow", default=None,
                    help="run the named workflow (mutually exclusive with --step)")
group.add_argument("--step", default=None,
                    help="run this one step and finish (mutually exclusive with --workflow)")
```

```python
def _submit(args: argparse.Namespace) -> int:
    ...
    workflow_name = args.workflow
    step = args.step
    if workflow_name is None and step is None:
        workflow_name = DEFAULT_WORKFLOW   # unchanged default: plain `harness submit` still targets the default pipeline
    if step is not None and invalid_step_name(step):
        print(f"error: invalid step name: {step!r}", file=sys.stderr)
        return 2
    task = Task(
        id=new_task_id(),
        created=SystemClock().now(),
        workflow_template=workflow_name,
        step=step,
        repository=args.repo,
        worktree=args.worktree,
        data=data,
    )
```

argparse's mutually-exclusive group rejects `--workflow X --step Y` together
at parse time — the explicitly-deferred combined-use case stays cleanly
unbuilt.

**`init`** (`cli.py:742-745`, `_init` at `cli.py:80-108`) — new
`--no-workflow` flag; `--workflow`'s current default and behavior are
unchanged so a plain `harness init` is byte-for-byte identical to today:

```python
init.add_argument("--workflow", default=DEFAULT_WORKFLOW)
init.add_argument("--no-workflow", action="store_true",
                   help="skip writing a default workflow; add steps under agents/ directly")
```

```python
def _init(args: argparse.Namespace) -> int:
    root = _root(args.root)
    layout = HarnessLayout(root)
    layout.agents.mkdir(parents=True, exist_ok=True)
    _write_default_repos(layout)

    if args.no_workflow:
        print(f"harness ready at {root} (no workflow — add steps under {layout.agents})")
        return 0

    # unchanged from here down: invalid_workflow_name check, write
    # DEFAULT_DEFINITION if missing, build(root, args.workflow),
    # _write_default_agents, print steps
```

**`run`** (`cli.py:756-776`, `_run` at `cli.py:649-690`) — `--workflow`'s
default changes from `DEFAULT_WORKFLOW` to `None`. Since queue discovery no
longer depends on which workflow is named (FR-7's union already discovers
every workflow file on disk), `--workflow` becomes purely informational —
what shows up as `Harness.workflow`/the `"started"` event. A harness
initialized the ordinary way (`workflows/default.json` present) behaves
identically whether or not `--workflow` is passed. A harness initialized with
`--no-workflow` can now `run` without ever naming a workflow that doesn't
exist — today's `build(root, args.workflow)` call
(`cli.py:669-681`) becomes `build(root, args.workflow, ...)` where
`args.workflow` may now be `None`.

`--github-workflow`/`--github-step` mirrors `submit`, applied to
`_github_sources` (`cli.py:339-378`):

```python
group = run.add_mutually_exclusive_group()
group.add_argument("--github-workflow", default=None)
group.add_argument("--github-step", default=None, dest="github_step")
```

```python
def _github_sources(args, root, registry, *, slug_of=github_slug, client=None) -> list[TaskSource]:
    ...
    workflow = args.github_workflow
    step = args.github_step
    if workflow is None and step is None:
        workflow = DEFAULT_WORKFLOW   # unchanged default
    ...
    sources.append(
        GithubTaskSource(
            client=client, clock=SystemClock(), repo=slug,
            workflow=workflow, step=step,
            repository=name, worktree_root=worktree_root,
            select_label=args.github_label, step_labels=DEFAULT_STEP_LABELS,
        )
    )
```

### `drivers/github_source.py`

`GithubTaskSource.__init__` (currently `github_source.py:27-41`,
`workflow: str = "default"`) gains `step: str | None = None` alongside it.
The `Task(...)` constructed in `poll()` (`github_source.py:78-97`) changes
from `workflow_template=self._workflow` (unconditional) to:

```python
Task(
    id=task_id,
    created=self._clock.now(),
    workflow_template=self._workflow,   # None when the source targets a step instead
    step=self._step,                   # None when the source targets a workflow instead
    repository=self._repository,
    worktree=f"{self._worktree_root}/{task_id}",
    dedup_key=dedup_key(self.kind, self._repo, issue.number),
    data={...},                        # unchanged
)
```

`self._step` is trusted as set only when `self._workflow` is `None` — the
driver itself does not re-validate the mutually-exclusive contract (the CLI
boundary already enforces it via `add_mutually_exclusive_group()`); this
keeps `GithubTaskSource` exactly as simple as it is today, still only ever
writing `data.source`, no new branching on outcome or status (invariants
19/20). Redesigning the per-issue label scheme for step-only issues stays
explicitly deferred (Open Questions).

### `api/templates/_task.html` (FR-9)

Current `workflow_template` row (`_task.html:10`) is the one nullable field
on this page without the `{{ x or "—" }}` fallback every neighboring row
already uses (`repository`, `worktree`, `status`, `lastOutcome`, `lockId` at
`_task.html:8-13`) — because `workflow_template` has never been null before
this change. Fix, plus a new `step` row directly below it:

```html
<tr><th>workflow</th><td>{{ task.workflow_template or "—" }}</td></tr>
<tr><th>step</th><td>{{ task.step or "—" }}</td></tr>
<tr><th>status</th><td>{{ task.status or "—" }}</td></tr>
```

(`status` row is shown for anchoring — it is unchanged, already has the
fallback.) A pipeline task (`workflow_template` set, `step=None`) now renders
its workflow name in the `workflow` cell and `—` in the new `step` cell —
unchanged in spirit from today, just with one more row. A workflow-less task
renders `—` / `development` respectively.

## Data schemas

### `Task` JSON (on disk: `tasks/*.json`, `queues/<step>/*.json`, `done/*.json`, `failed/*.json`)

```jsonc
{
  "id": "tsk_...",
  "repository": "myrepo",           // nullable, unchanged
  "worktree": null,                 // nullable, unchanged
  "workflowTemplate": "default",    // NOW NULLABLE — null/absent for a workflow-less task
  "step": null,                     // NEW — entry step for a workflow-less task; null when workflowTemplate is set
  "status": null,
  "lastOutcome": null,
  "lockId": null,
  "dedupKey": null,
  "created": "2026-07-21T10:00:00Z",
  "history": [ /* unchanged HistoryEntry[] */ ],
  "data": {}
}
```

Exactly one of `workflowTemplate`/`step` is non-null on a freshly submitted
task — enforced at the boundaries (CLI's mutually exclusive group,
`GithubTaskSource`'s constructor contract), not by `Task` itself (the model
stays a plain data holder; `router.route()` is the one place that
defensively handles both together, row 3 of the truth table above). A task
that has moved past its first step (`status` set) carries
`workflowTemplate`/`step` unchanged from submission — both are read-only
after creation, exactly like `repository`/`worktree` today.

Backward compatibility: an existing task file on disk (always carrying a
non-null `workflowTemplate`, never carrying a `step` key) loads unchanged —
`raw.get("workflowTemplate")` returns the same string it always did,
`raw.get("step")` defaults to `None`, which is exactly the workflow-carrying
task's expected `step` value.

### Workflow JSON (`workflows/*.json`) — unchanged

```jsonc
{
  "name": "default",
  "start": "plan",
  "transitions": [
    {"from": "plan", "on": "done", "to": "design"}
  ]
}
```

### Agent JSON (`agents/*.json`) — unchanged shape

Its *presence* now additionally matters for queue discovery (FR-7) whenever
a real (`--agent claude`) run is used: a standalone agent file with no
workflow referencing it now gets a queue and a consumer purely by existing on
disk. Under `--agent dummy`, discovery is workflow-files-only (no catalog is
wired) — matching today's dummy-run behavior exactly (see Out-of-scope note
below).

### Board (`BoardView` / API) — unchanged wire shape

`GET /board` keeps returning `{revision, columns: [{name, tasks}]}`. Only the
*set and order* of `columns` changes per FR-8 — no new fields. The task
detail page (server-rendered HTML, not JSON) gains one row (FR-9), covered
above.

## Sequencing (binding on the dev step, per plan-02's NFR)

1. **`ports/{workflows,agent}.py` + every concrete implementation together,
   first.** `WorkflowRepository.names()`/`AgentCatalog.names()` land as
   abstract methods in the *same commit* as their four concrete
   implementations (`FilesystemWorkflowRepository`,
   `MemoryWorkflowRepository`, `FilesystemAgentCatalog`,
   `MemoryAgentCatalog`) and `invalid_step_name`. Landing the abstract method
   alone first breaks every existing `MemoryAgentCatalog(...)`/
   `MemoryWorkflowRepository(...)` call site with `TypeError` at collection
   time.
2. **`models.py`** — field reorder, `Task.step`, `to_dict`/`from_dict`.
3. **`router.py`** — the 8-row truth table above (depends on `models.py`
   only, per the router-purity invariant).
4. **`dispatcher.py`** — the conditional workflow lookup (depends on
   `router.py`'s new signature).
5. **`app.py`** — the union queue-discovery rule (depends on step 1's
   `.names()` methods and step 2's optional `workflow_template`).
6. **`projection.py`** — `column_order`/`BoardProjection` signature change
   (depends on step 5 supplying `steps`/`workflows` at `build()` time).
7. **`cli.py`** — `submit`/`init`/`run` flags, `invalid_step_name` at the
   `submit` boundary (depends on steps 1-5).
8. **`drivers/github_source.py`** — optional `step` param (depends on step
   2).
9. **`api/templates/_task.html`** — FR-9 fallback + `step` row (depends on
   step 2 only; independent of 3-8, could land any time after).
10. **`CLAUDE.md` invariant 8** reworded in the same PR, not deferred, to:
    "solely on `(status, lastOutcome)`, and — since this task — whether a
    workflow is present at all; never on `repository`/`worktree`, `step`, or
    `data`."

## Out of scope (confirmed unchanged from plan-02)

- Redesigning `GithubTaskSource`'s per-issue label scheme for step-only
  issues.
- Letting a task switch workflows mid-flight or run against more than one at
  once.
- Any change to the wire format of workflow JSON files.
- `--agent dummy` + a workflow-less `--step` task getting its own queue when
  the step name doesn't already appear in some workflow file's `steps()` —
  this is a pre-existing asymmetry of the dummy path (it has no notion of
  "declared steps" independent of a workflow file); do not build an
  `extra_steps` escape hatch for it unless a test actually needs one later.
- Allowing `--workflow` and `--step` together on `submit`/`run`. The
  mutually-exclusive argparse group makes this cleanly unbuilt.
