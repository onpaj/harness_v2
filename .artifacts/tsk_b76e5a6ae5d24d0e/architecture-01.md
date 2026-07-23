# Architecture: Make workflows optional

## Verdict on the design

I read `plan-01.md` and `design-01.md` against the current source
(`models.py`, `router.py`, `dispatcher.py`, `app.py`, `projection.py`,
`cli.py`, `ports/{workflows,agent}.py`, `drivers/{fs_workflows,fs_agents,
memory,github_source}.py`, `test_architecture.py`). The design is accurate —
every code snippet it quotes matches what's actually on disk today, and its
resolution of FR-7 (union of `WorkflowRepository.names()` and
`AgentCatalog.names()`) is the right call. I'm endorsing the design as the
implementation contract, with the additions below: three gaps the design
didn't surface (one of them a hard blocker — an ABC instantiation failure),
plus a few explicit implementation decisions where the design left room for
interpretation.

## Alignment with existing patterns

This change is a pure generalization along an axis the codebase already
treats as a driver seam:

- **The `Workflow | None` split follows invariant 4 to the letter.** `route()`
  gains a parameter, not a lookup — it stays a pure function of `(Task,
  Workflow | None)`. No new import lands in `router.py`, so
  `test_router_only_knows_models` (test_architecture.py:22) keeps passing
  unmodified.
- **`.names()` on `WorkflowRepository`/`AgentCatalog` mirrors an existing
  shape.** `RepositoryRegistry` already has `.names()` (`drivers/fs_repos.py`,
  used today by `cli._github_sources`) for exactly this purpose — enumerate
  what's on disk without raising. Extending the same method name to the other
  two repositories is consistent, not novel.
- **The dispatcher's conditional workflow lookup is the same shape as its
  existing `WorkflowNotFound` handling** (`dispatcher.py:59-63`) — it grows an
  `if`, not a new code path. `_move`/`_finish`/`_fail` are untouched, so
  invariant 3 (dispatcher owns "where next" exclusively) holds structurally,
  not just by intent.
- **`consumer.py` needs zero changes.** It already treats `task.status` as an
  opaque label handed to it by the dispatcher (`consumer.py:40`,
  `self._step`) and never reads `workflow_template`. Confirmed by grep: the
  only files touching `workflow_template` today are `models.py`, `cli.py`,
  `dispatcher.py`, `drivers/{memory,github_source}.py`, and one Jinja
  template. That is exactly the surface the plan's "in scope" list names —
  there's no hidden fourth consumer of the field.

## Proposed architecture

No new components. This is a schema-and-routing change layered onto the
existing five-layer module map (models → router → dispatcher → app → cli),
plus the two repository ports it reads from. The key decision is FR-7's, and
it's already made:

**Queue discovery = `WorkflowRepository.names()` steps ∪ `AgentCatalog.names()`.**
Alternatives considered (per the plan): agent-catalog-only (rejected — breaks
every in-memory test caller and the `--agent dummy` path, both of which have
no `agents/` directory at all) and status quo single-workflow `.steps()`
(rejected — it's exactly the constraint FR-7 exists to remove). The union is
strictly backward compatible: with one workflow file and no catalog wired, it
degenerates to today's set exactly. I have nothing to add here beyond
endorsing it — it's the smallest change that satisfies FR-7 without breaking
a caller that has never heard of `agents/`.

## Implementation guidance

Follow the design's component-by-component breakdown as written, with these
corrections and additions:

### 1. Blocker: `MemoryAgentCatalog` must gain `.names()`

`ports/agent.py`'s `AgentCatalog` is an `ABC`. The design correctly adds
`names()` as a new `@abstractmethod` on it, and correctly calls out that
`FilesystemAgentCatalog.names()` and `MemoryWorkflowRepository.names()` need
implementing. **It misses that `MemoryAgentCatalog` (`drivers/memory.py:297`)
also subclasses `AgentCatalog` and only implements `get()`.** The moment
`names()` becomes abstract, `MemoryAgentCatalog(...)` raises `TypeError: Can't
instantiate abstract class` at every call site that constructs one — which
includes any test built around the phase-3 agent catalog. This is not
optional polish, it's a prerequisite: add

```python
def names(self) -> list[str]:
    return list(self._specs)
```

to `MemoryAgentCatalog` in the same commit that adds the abstract method,
before any test that constructs one can even be collected.

`MemoryWorkflowRepository.names()` (already flagged by the design) is the
same shape: `return tuple(self._workflows)`.

### 2. The board template renders `None` literally for a workflow-less task

`api/templates/_task.html:10` has:

```
<tr><th>workflow</th><td>{{ task.workflow_template }}</td></tr>
```

Every other nullable field on that page uses the `{{ x or "—" }}` fallback
(`repository`, `worktree`, `status`, `lastOutcome`, `lockId`). This one
doesn't, because `workflow_template` has never been null before. Once it can
be, the detail page for a workflow-less task will render the literal string
`None` in that cell — confusing, and inconsistent with every neighboring row.
Fix in the same PR:

```
<tr><th>workflow</th><td>{{ task.workflow_template or "—" }}</td></tr>
<tr><th>step</th><td>{{ task.step or "—" }}</td></tr>
```

Not called out in the plan's or design's "in scope" list (`api/` wasn't
named), but it's a one-line consequence of the schema change and belongs with
it rather than as a follow-up — an untested template is exactly where a stray
`None` survives to production.

### 3. Guard `--step` against the two reserved names

`END = "end"` and `FAILED = "failed"` are reserved terminal names (invariant
in `models.py:9-15`); `Workflow.steps()` already excludes `END` by
construction, and `invalid_workflow_name` protects workflow *names* from path
traversal and empty strings but was never asked to protect step names,
because a step name previously only ever came from inside a trusted workflow
file. `Task.step` changes that: it now comes straight from `--step` (a CLI
arg) or a `TaskSource`. A task submitted with `--step end` or `--step failed`
either silently fails to route (no queue named that unless someone also wrote
`agents/end.json`) or, worse, produces a queue and board column literally
called `end`/`failed` that collides with the reserved terminal columns
`column_order` already appends. Add a check next to
`invalid_workflow_name`/`_invalid_agent_name` — same shape, one new
constant list:

```python
def invalid_step_name(name: str) -> bool:
    return invalid_workflow_name(name) or name in (END, FAILED)
```

used in `cli._submit` (reject `--step end`) and worth a one-line defensive
check in `router.route()`'s new branch too (`task.step in (None, END,
FAILED)` → `Failed(...)`, since the router is the one place that already
defends against malformed input for the workflow-present path — the
`status`-unknown-to-workflow check in FR-4 is the same category of guard).

### 4. Everything else: build to the design as written

- `models.py`: field reorder (`id`, `created` ahead of `workflow_template`) is
  safe — confirmed every construction site in the repo (`cli.py`,
  `drivers/memory.py`, `drivers/github_source.py`) already uses keyword
  arguments exclusively; grep found no positional `Task(...)` call.
- `router.py`: implement the truth table exactly as tabulated in
  design-01.md — it is a faithful, exhaustive superset of the current
  four-branch function (verified line by line against `router.py:8-32`).
- `dispatcher.py`: the conditional lookup at `dispatcher.py:59-63` is the only
  edit; nothing downstream of `route()` changes.
- `app.py`: `build()`'s `workflow_name` becomes `str | None`; `Harness.workflow`
  becomes `Workflow | None`; the `"started"` event's `workflow=` field follows
  suit (`app.py:166`). `HarnessLayout`, `recover()`, `_seed_pollers()`, and all
  three loops in `run()` need no change — confirmed they only ever touch
  `self._step_queues` (a plain dict), never `self.workflow`.
- `projection.py`: `column_order`/`BoardProjection.__init__` take
  `(steps, workflows=())` instead of a single `Workflow`. `hydrate`/`apply`/
  `snapshot`/`_store`/`_bump` are untouched — they already work purely off
  `self._order`, never off a `Workflow` object (confirmed at
  `projection.py:84-141`).
- `cli.py`: mutually-exclusive `--workflow`/`--step` on `submit`, mirrored as
  `--github-workflow`/`--github-step` on `run`. No existing subcommand uses
  `add_mutually_exclusive_group()` today (grep confirms), so this is a new
  argparse pattern in this file — keep the `help=` text on both flags
  explicit about the alternative, matching the existing style of
  `run.add_argument("--agent", choices=..., help="...")`.
- `drivers/github_source.py`: `step: str | None = None` constructor param,
  `self._step` wins over `self._workflow` when both happen to be set (trusts
  the CLI boundary, per the plan's accepted trade-off).

### Data flow (unchanged shape, new branch)

```
CLI/TaskSource → Task{workflow_template | step} → inbox
                                                     │
                                          Dispatcher.tick()
                                                     │
                              workflow = get(template) if template else None
                                                     │
                                        route(task, workflow)  [pure]
                                          │        │         │
                                     MoveTo(step) Finished  Failed
                                          │        │         │
                                    step_queues   done      failed
```

The only new edge is `workflow=None` flowing into `route()` — everything
before and after it is the existing pipeline.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `MemoryAgentCatalog` missing `.names()` breaks every test that builds one, the instant the abstract method lands | Land it in the same commit as the `AgentCatalog.names()` abstract method (§1) — sequence this first in the dev step, not last |
| Board renders `None` for a workflow-less task's detail page | One-line template fix (§2), same PR |
| `--step end`/`--step failed` produces a confusing reserved-name collision | `invalid_step_name` guard at the CLI boundary and in `router.route()` (§3) |
| FR-4's accepted trade-off: a workflow-less task with a hand-corrupted `status` silently finishes instead of failing | Already flagged and accepted by the plan (no workflow ever had a membership set to check `status` against, so this isn't a regression) — no action needed, just don't try to "fix" it by inventing a synthetic membership set, that would reintroduce state the router doesn't have |
| `--agent dummy` + a workflow-less `--step` task has no queue unless the step name happens to also appear in some workflow file | Explicitly out of scope per the plan/design (dummy path is for exercising pipeline shape, not bespoke single-step targets); do not build the `extra_steps` escape hatch mentioned as a follow-up unless a test actually needs it |
| CLAUDE.md invariant 8 ("solely on `(status, lastOutcome)`") becomes imprecise once `route()` also branches on workflow-presence | Reword to: "solely on `(status, lastOutcome)`, and — since this task — whether a workflow is present at all; never on `repository`/`worktree`, `step`, or `data`." Do this as part of the same PR (plan's rough-plan step 8 already flags it) |

## Prerequisites before implementation begins

1. Add `.names()` to `MemoryAgentCatalog` and `MemoryWorkflowRepository`
   (`drivers/memory.py`) in the same change that adds the abstract methods —
   this is a hard ordering dependency, not a nice-to-have (§1).
2. No other `AgentCatalog`/`WorkflowRepository` subclasses exist in the repo
   (grep confirms exactly three implementers of each: filesystem, memory, and
   the ABC itself) — nothing else needs updating for the new abstract method.
3. Nothing else blocks starting; models/router/dispatcher/app/projection/cli
   can be implemented in the order the plan's rough plan already lists (1→7),
   since each layer's tests can be written against the layer below once that
   layer's `.names()`/optional-field support lands.
