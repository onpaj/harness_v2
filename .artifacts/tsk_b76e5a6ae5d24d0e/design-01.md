# Design: Make workflows optional

This feature has no new user interface. It touches the CLI (flags) and the
existing board's column set, but introduces no new interaction model — the
UX/UI section is omitted per the plan's explicit scope ("UI visual redesign
beyond column-ordering correctness" is out of scope). Everything below is
component design and data schema.

## Guiding decision: what "queue discovery" means (resolves FR-7's open question)

The plan's recommended default — "derive queues purely from `agents/*.json`"
— cannot be the *whole* rule: today's in-memory/test callers of `build()`
(`tests/test_app.py` and friends) create a workflow file and nothing else, and
the dummy-behavior path (`--agent dummy`, `build()` with no `catalog`) never
populates an `agents/` directory at all. A pure agent-only rule would silently
produce zero queues for every one of those.

**Decision:** the live step-queue set is the **union** of two enumerations,
each newly exposed as a `.names()` method on the existing repository ports:

- every step reachable in **every workflow file** under `workflows/`
  (`WorkflowRepository.names()` → `.get(name).steps()` for each), and
- every agent declared under `agents/` (`AgentCatalog.names()`), when a
  catalog is wired in.

This is a strict generalization of today's behavior (which is "the steps of
*one* workflow"): with exactly one workflow file and no catalog, the union
degenerates to exactly what `build()` computes today. It also directly
satisfies FR-7's rationale — several different workflows and standalone
agents can share one running harness — without breaking any caller that
never touches `agents/`.

Both `.names()` methods are best-effort for discovery: a workflow file that
fails to parse is skipped during the union walk (it only becomes a hard error
when a task actually names it, exactly as `WorkflowNotFound` already works
today). The `workflow_name` still optionally passed to `build()` keeps its
narrow, current meaning — "load and validate *this* one eagerly, expose it as
`Harness.workflow`" — but it is no longer the sole source of the queue set.

## Component design

### `models.py`

`Task` gains one field and loses a hard requirement:

```python
@dataclass(frozen=True)
class Task:
    id: str
    created: str
    workflow_template: str | None = None   # was: required str
    step: str | None = None                # new: entry step for a workflow-less task
    repository: str | None = None
    worktree: str | None = None
    status: str | None = None
    last_outcome: str | None = None
    lock_id: str | None = None
    dedup_key: str | None = None
    history: tuple[HistoryEntry, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
```

`id` and `created` move ahead of `workflow_template` so the dataclass field
order stays valid once `workflow_template` gets a default (Python requires
non-default fields before default ones). Every construction site in the repo
already uses keyword arguments, so this reorder is not a breaking change to
any caller.

`to_dict`/`from_dict`: add `"step": self.step` to the serialized shape;
`workflowTemplate` is read with `raw.get("workflowTemplate")` (was
`raw["workflowTemplate"]`) so both old files (always carrying a string) and
new workflow-less files (carrying `null` or omitting the key) load
identically. `step` is read with `raw.get("step")`, defaulting to `None` for
every task written before this change — fully backward compatible on disk.

`Workflow` is unchanged in shape. `Workflow.steps()` (already computes "every
step that needs a queue") becomes the membership set FR-4's check is judged
against — no change needed there, it already includes a step that is only
ever a `to_step` or the bare `start`.

### `router.py`

```python
def route(task: Task, workflow: Workflow | None) -> Decision:
```

Truth table (replaces the current four-branch function):

| `task.status` | `workflow` | `last_outcome` | Decision |
|---|---|---|---|
| `None` | given | – | `MoveTo(workflow.start)` (unchanged) |
| `None` | `None` | – | `MoveTo(task.step)` if `task.step` set, else `Failed("task has neither a workflow nor a step")` |
| set | any | `None` | `Failed("task has status {status!r} but no lastOutcome")` (unchanged) |
| set | `None` | set | `Finished()` — always (FR-2) |
| set | given | set, `status` unknown to `workflow.steps()` | `Failed("workflow {name!r} has no step {status!r}")` (FR-4, preserved) |
| set | given | set, `status` known, no edge for `(status, outcome)` | `Finished()` (FR-3 — changed from `Failed`) |
| set | given | set, edge target is `END` | `Finished()` (unchanged) |
| set | given | set, edge target is another step | `MoveTo(target)` (unchanged) |

The function stays pure (invariant 4): it takes the `Workflow | None` the
caller already resolved, does no lookup itself. This is exactly the "two-field
design" the plan chose in its open questions, kept here: the router never
consults a repository, so the dispatcher remains the only place that touches
`WorkflowRepository`.

### `dispatcher.py`

One line changes in `tick()`:

```python
workflow = None
if task.workflow_template is not None:
    try:
        workflow = self._workflows.get(task.workflow_template)
    except WorkflowNotFound as error:
        self._fail(task, str(error))
        return True

decision = route(task, workflow)
```

Everything downstream (`_move`/`_finish`/`_fail`, event emission, history)
is unchanged — the dispatcher still owns "where next" exclusively (invariant
3), it just now sometimes routes with `workflow=None`.

### `ports/workflows.py` / `drivers/fs_workflows.py`

New method on the port:

```python
class WorkflowRepository(ABC):
    @abstractmethod
    def get(self, name: str) -> Workflow: ...
    @abstractmethod
    def names(self) -> tuple[str, ...]:
        """Every workflow name discoverable without raising. Best-effort:
        a name whose file fails to parse is simply absent from the result."""
```

`FilesystemWorkflowRepository.names()` globs `<root>/*.json`, keeps stems
that pass `not invalid_workflow_name(...)`, sorted. `MemoryWorkflowRepository`
(test double) returns `tuple(self._workflows)`.

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

`FilesystemAgentCatalog.names()` globs `<root>/*.json`, filters
`not _invalid_agent_name(...)`, sorted.

### `app.py`

`build()`'s signature: `workflow_name: str | None = None` (was required
positional `str`).

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
        continue          # unreadable file: skip during discovery, not fatal
if catalog is not None:
    known_steps |= set(catalog.names())

steps = tuple(sorted(known_steps))
step_queues = {
    step: FilesystemTaskQueue(name=step, root=layout.queues / step, events=events, quarantine=failed)
    for step in steps
}
```

`projection = BoardProjection(steps=steps, workflows=[workflows.get(n) for n in workflows.names() if loadable])`
— see below.

`Harness.workflow` becomes `Workflow | None`; the `"started"` event now emits
`workflow=self.workflow.name if self.workflow else None`.

Nothing else in `Harness` changes — `recover()`, `_seed_pollers()`, the three
loops in `run()` already work off `self._step_queues` (a plain dict), never
off `self.workflow`.

### `projection.py`

```python
def column_order(steps: Iterable[str], workflows: Iterable[Workflow] = ()) -> tuple[str, ...]:
    """Steps ordered by reachability from every workflow's start (in the
    order the workflows are given), then every remaining known step
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
```

`BoardProjection.__init__(self, steps: Iterable[str], workflows: Iterable[Workflow] = ())`
replaces `__init__(self, workflow: Workflow)`. `hydrate()`/`apply()`/
`snapshot()` are unchanged — they already work purely off `self._order`
and per-task columns, never off a `Workflow` object.

**FR-8 check:** a workflow-less task submitted with `--step development`
produces `steps=(..., "development", ...)` and `workflows=()` (if no
workflow file exists) or `workflows=(<whatever's on disk>,)`. Either way
`"development"` lands in `order` via the fallback loop if no workflow's walk
reaches it, giving exactly `todo → development → done` for that task, while
a pipeline task's columns are unaffected because its steps are still walked
in workflow order first.

### `cli.py`

**`submit`** — mutually exclusive `--workflow` / `--step`:

```python
group = submit.add_mutually_exclusive_group()
group.add_argument("--workflow", default=None)
group.add_argument("--step", default=None)
```

```python
def _submit(args):
    ...
    workflow_name = args.workflow
    step = args.step
    if workflow_name is None and step is None:
        workflow_name = DEFAULT_WORKFLOW      # unchanged default: plain `harness submit` still targets the default pipeline
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

`argparse`'s mutually-exclusive group rejects `--workflow X --step Y`
together at parse time — the plan's flagged-but-not-built combined-use case
stays unbuilt, cleanly.

**`init`** — new `--no-workflow` flag, `--workflow` keeps its current
default so a plain `harness init` is byte-for-byte the same as today:

```python
init.add_argument("--workflow", default=DEFAULT_WORKFLOW)
init.add_argument("--no-workflow", action="store_true")
```

```python
def _init(args):
    root = _root(args.root)
    layout = HarnessLayout(root)
    layout.agents.mkdir(parents=True, exist_ok=True)
    _write_default_repos(layout)

    if args.no_workflow:
        print(f"harness ready at {root} (no workflow — add steps under {layout.agents})")
        return 0

    # unchanged from here: write DEFAULT_DEFINITION if missing, build(), _write_default_agents, print steps
```

**`run`** — `--workflow` default changes from `DEFAULT_WORKFLOW` to `None`.
This is the one deliberate, visible CLI default change: since the live queue
set is now the union across every workflow file already on disk (see the
guiding decision above), `--workflow` no longer needs to *carry* the pipeline
for `run` to find its steps — it only names the workflow exposed on
`Harness.workflow` (informational: the `"started"` event, nothing that
affects routing or queues). A harness initialized the ordinary way (workflow
file present) behaves identically whether or not `--workflow` is passed,
because `workflows/default.json` is discovered either way. A harness
initialized with `--no-workflow` can now be `run` without ever naming a
workflow that doesn't exist.

**`--github-workflow` / `--github-step`** on `run`, mirroring `submit`:

```python
group = run.add_mutually_exclusive_group()
group.add_argument("--github-workflow", default=None)
group.add_argument("--github-step", default=None, dest="github_step")
```

`_github_sources()`:

```python
workflow = args.github_workflow
step = args.github_step
if workflow is None and step is None:
    workflow = DEFAULT_WORKFLOW   # unchanged default
...
GithubTaskSource(..., workflow=workflow, step=step, ...)
```

### `drivers/github_source.py`

`GithubTaskSource.__init__` gains `step: str | None = None`. In `poll()`,
the constructed `Task` becomes:

```python
Task(
    id=task_id,
    created=self._clock.now(),
    workflow_template=self._workflow,   # None when the source targets a step
    step=self._step,                    # None when the source targets a workflow
    repository=self._repository,
    ...
)
```

`self._step` wins when set (the driver trusts the CLI's mutually-exclusive
validation upstream); this keeps the driver itself simple, matching invariant
19/20's spirit — `GithubTaskSource` still only ever writes `data.source`, it
doesn't gain any new branching on outcome or status. Redesigning the
per-issue label scheme for step-only issues (e.g. a distinct select label per
target) stays explicitly out of scope, as the plan noted; this only wires the
already-existing `select_label`/`step_labels` mechanism to an optional
`workflow_template=None` task.

## Data schemas

### `Task` JSON (on disk, `tasks/*.json`, `queues/<step>/*.json`, etc.)

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

Exactly one of `workflowTemplate` / `step` is non-null on a freshly submitted
task — enforced at the boundaries (CLI's mutually exclusive group,
`GithubTaskSource`'s constructor contract), not by the model itself (the
model stays a plain data holder, no validation logic per the existing
convention — `router.route()` is the one place that defensively handles both
being `None`).

A task that *has* moved past its first step (`status` set) carries
`workflowTemplate`/`step` unchanged from submission — they are read-only
after creation, exactly like `repository`/`worktree` today (invariant 8:
`route()`/dispatcher still decide solely on `(status, lastOutcome)`, plus now
the presence/absence of a resolved `Workflow`, never on `step` directly).

### Workflow JSON (`workflows/*.json`)

Unchanged:

```jsonc
{
  "name": "default",
  "start": "plan",
  "transitions": [
    {"from": "plan", "on": "done", "to": "design"}
  ]
}
```

### Agent JSON (`agents/*.json`)

Unchanged shape; its *presence* now additionally matters for queue discovery
(FR-7) whenever a real (`--agent claude`) run is used — a standalone agent
file with no workflow referencing it now gets a queue and a consumer purely
by existing on disk. Under `--agent dummy`, discovery is workflow-files-only
(no catalog is wired), matching today's dummy-run behavior exactly unless a
`--step`-only task also needs a queue, which the operator gets by simply
having a workflow file (even a trivial one-step one) or, for the dummy path
specifically, by the fact that its step already appears in some workflow's
`steps()` on disk — see Open follow-up below.

### Board (`BoardView` / API) — unchanged wire shape

`GET /board` keeps returning `{revision, columns: [{name, tasks}]}`. Only the
*set and order* of `columns` changes per FR-8 — no new fields.

## Open follow-up flagged during design (not built here)

- **`--agent dummy` + workflow-less task.** Queue discovery for the dummy
  path only consults `workflows/*.json` (no catalog exists to consult). A
  workflow-less task submitted with `--step foo` under `--agent dummy` only
  gets a queue if `"foo"` happens to already appear in some workflow file's
  `steps()`. This is a pre-existing asymmetry of the dummy path (it has never
  had its own notion of "declared steps" — `DummyBehavior` handles whatever
  step it's handed) and is judged acceptable: the dummy path exists for
  testing the pipeline shape, not for exercising bespoke single-step targets;
  real single-step usage goes through `--agent claude` where `agents/*.json`
  is authoritative. If this needs closing later, the fix is small: give
  `DummyBehavior`'s wiring in `build()` an explicit `extra_steps:
  Iterable[str]` for tests that want a workflow-less dummy queue without a
  matching workflow file.
- **GitHub label scheme for step-only issues** — unchanged from the plan:
  left for a follow-up task. This design only wires the constructor-level
  optionality (`step` param) so that follow-up isn't blocked on model/router
  work.
