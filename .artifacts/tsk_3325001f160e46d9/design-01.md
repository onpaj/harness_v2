# Design: serve multiple workflows in a single running harness

Input: `plan-01.md` (FR-1..FR-6, NFR, data model, interfaces, open questions).
This feature has no user interface — `harness run` is a CLI/daemon and the board
API (`api/`, `ports/board.py`) is explicitly out of scope for shape changes
(plan-01 "Board/API: no change"). The UX/UI section is therefore omitted.

## Component design

### Overview — who owns the "served set"

```
cli.py (run subcommand)
   │  resolves NAME LIST from --workflow*/--all-workflows
   │  (the only place that turns CLI flags into a `tuple[str, ...]`)
   ▼
app.build(root, workflows: str | Sequence[str], ...)
   │  1. normalizes to tuple[str, ...] names
   │  2. resolves each via the RAW FilesystemWorkflowRepository (fail fast,
   │     same error path as today for one name)
   │  3. builds step_queues as the UNION of every resolved workflow's steps
   │  4. wraps the repository in ServedWorkflowRepository(raw, names) and
   │     hands ONLY the wrapped one to Dispatcher
   │  5. builds BoardProjection([...resolved workflows in served order...])
   ▼
Harness(workflows: dict[str, Workflow], step_queues, dispatcher, consumers, ...)
```

The served set is decided once, at `build()` time, and is fixed for the
process's lifetime (plan-01, out of scope: no reload). Nothing downstream of
`build()` re-derives it — `Dispatcher` only ever sees the wrapped repository,
`BoardProjection` only ever sees the resolved `Workflow` objects it was
constructed with.

### `ports/workflows.py` — `WorkflowRepository.names()`

New abstract method, mirroring `RepositoryRegistry.names()` (`ports/repos.py:23-26`):
lenient on enumeration, strict on resolution.

```python
class WorkflowRepository(ABC):
    @abstractmethod
    def get(self, name: str) -> Workflow: ...

    @abstractmethod
    def names(self) -> tuple[str, ...]:
        """All workflow names discoverable at the root. An unreadable or
        missing definition is skipped here (still fails loud from get() if
        actually requested)."""
```

Any existing `WorkflowRepository` implementation besides
`FilesystemWorkflowRepository` (none exist yet in this codebase) would need
to add this method — there is exactly one production implementation today,
so this is not a fan-out concern.

### `drivers/fs_workflows.py` — two additions

**1. `FilesystemWorkflowRepository.names()`**

```python
def names(self) -> tuple[str, ...]:
    if not self._root.is_dir():
        return ()
    return tuple(sorted(p.stem for p in self._root.glob("*.json")))
```

`self._root` is already `<harness-root>/workflows` (`app.py:233`,
`HarnessLayout.workflows`), so this globs directly, no extra path segment.
Sorted for determinism (`--all-workflows` output, error messages). No
`.get()` call here — a broken file is invisible to `names()` and only
surfaces if something actually asks `.get()` for it (same "quiet on
discovery, loud on resolve" split as `RepositoryRegistry.names()`).

**2. `ServedWorkflowRepository` — a decorator, not a subclass**

```python
class ServedWorkflowRepository(WorkflowRepository):
    """Restricts an inner repository to a fixed served set.

    A name outside the set fails the same way a nonexistent one does
    (WorkflowNotFound) — the dispatcher's except-clause at dispatcher.py:60-62
    already handles that failure mode, so no dispatcher change is needed.
    """

    def __init__(self, inner: WorkflowRepository, names: Sequence[str]) -> None:
        self._inner = inner
        self._served = tuple(names)

    def get(self, name: str) -> Workflow:
        if name not in self._served:
            served = ", ".join(self._served) or "(none)"
            raise WorkflowNotFound(
                f"workflow {name!r} is not served by this harness "
                f"(served: {served})"
            )
        return self._inner.get(name)

    def names(self) -> tuple[str, ...]:
        return self._served
```

Placed in `drivers/fs_workflows.py` alongside `FilesystemWorkflowRepository`
— it wraps *any* `WorkflowRepository`, but the only caller is `app.build()`,
and it's a driver-level detail (invariant: neither `dispatcher.py` nor
`consumer.py` import `drivers/`; they only ever see the `WorkflowRepository`
port). No new file needed for one ~15-line class.

`build()` always resolves the served workflows through the *raw*
(unwrapped) repository first — `ServedWorkflowRepository` would reject its
own served names is not the risk; the risk is a chicken-and-egg `get()` on
a repository that hasn't been told what it serves yet. So the sequence is:
resolve names → build `served = ServedWorkflowRepository(raw, names)` →
hand `served` to `Dispatcher`. `raw` itself never leaves `build()`.

### `projection.py` — `BoardProjection` and `column_order` take a sequence

```python
def column_order(workflows: Sequence[Workflow]) -> tuple[str, ...]:
    """Union of each workflow's own reachability order, first-seen wins.

    workflows[0]'s reachable steps (in its own order) come first, then
    workflows[1]'s not-yet-seen steps, and so on — never workflows[i]'s
    order.
    """
    order: list[str] = []
    for workflow in workflows:
        for step in _reachable_order(workflow):   # today's per-workflow BFS body
            if step not in order:
                order.append(step)
    return (TODO_COLUMN,) + tuple(order) + (DONE_COLUMN, FAILED_COLUMN)


class BoardProjection(BoardView):
    def __init__(self, workflows: Sequence[Workflow]) -> None:
        self._order = column_order(workflows)
        ...  # unchanged below this line
```

The existing per-workflow BFS body (`projection.py:31-45`) is extracted
unchanged into a helper (`_reachable_order`); `column_order` becomes a thin
loop over workflows calling it. `hydrate`/`apply`/`snapshot`/`get`/
`subscribe`/`_store`/`_bump` are untouched — they already operate purely on
`self._order` and don't know how many workflows produced it.

Single-workflow callers become `BoardProjection([workflow])` — a one-element
list, not a new code path. `app.build()` passes the list of resolved
`Workflow` objects in the same order their names were resolved (first
`--workflow` flag first; `--all-workflows` uses the sorted order from
`names()`), satisfying FR-4's "first workflow's steps, in served order, then
the next workflow's not-yet-seen steps" rule directly from list order — no
separate merge step is needed beyond the loop above.

### `app.py` — `Harness` and `build()`

**`Harness`**: `workflow: Workflow` → `workflows: dict[str, Workflow]`
(name → resolved `Workflow`, insertion-ordered = served order). The
`started` event changes accordingly (see Data schemas below). Every other
constructor parameter is unchanged.

**`build()`**: second positional parameter widens from `workflow_name: str`
to `workflows: str | Sequence[str]`. Body changes:

```python
def build(
    root: Path,
    workflows: str | Sequence[str],
    *,
    ...  # unchanged kwargs
) -> Harness:
    ...
    names = (workflows,) if isinstance(workflows, str) else tuple(workflows)

    repo = FilesystemWorkflowRepository(layout.workflows)
    resolved = {name: repo.get(name) for name in names}   # fail-fast, dict preserves order
    served_repo = ServedWorkflowRepository(repo, names)

    projection = BoardProjection(list(resolved.values()))
    ...
    step_queues = {
        step: FilesystemTaskQueue(...)
        for workflow in resolved.values()
        for step in workflow.steps()
    }   # dict comprehension de-dupes by step name automatically (FR-3)
    ...
    dispatcher = Dispatcher(..., workflows=served_repo, ...)   # wrapped repo, not `repo`
    ...
    return Harness(..., workflows=resolved, ...)
```

A single string keeps behaving exactly as `workflow_name: str` does today —
this is the FR-2 guarantee, and it's structural (the `isinstance` branch),
not a convention someone has to remember.

`behavior_for(step)` (`app.py:283-296`) is unchanged: it is already keyed by
step name alone, which is exactly FR-3's "one queue, one consumer, one
behavior per step name, workflow-agnostic" rule — no code there references
a workflow.

### `cli.py` — `run` subcommand

```
run.add_argument("--workflow", action="append", dest="workflows", default=None)
run.add_argument("--all-workflows", action="store_true")
```

Resolution in `_run()`, before calling `build()`:

```python
def _resolve_served_workflows(args, layout) -> tuple[str, ...]:
    if args.workflows and args.all_workflows:
        raise SystemExit_-style CLI error: "--workflow and --all-workflows are mutually exclusive"
    if args.all_workflows:
        names = FilesystemWorkflowRepository(layout.workflows).names()
        if not names:
            error: no workflow definitions found under <layout.workflows>
        return names
    return tuple(args.workflows) if args.workflows else (DEFAULT_WORKFLOW,)
```

This mirrors the existing `WorkflowNotFound` → `print(..., file=sys.stderr); return 2`
pattern already used around `build()` in both `_init` and `_run`
(`cli.py:97-101`, `682-684`) — the mutual-exclusivity and empty-discovery
checks return `2` the same way, before `build()` is even called.

`init`/`submit` subcommands: **untouched**. They keep a single
`--workflow` (default `DEFAULT_WORKFLOW`) and call `build(root, args.workflow)`
— a bare string, hitting the `isinstance(workflows, str)` branch in `build()`.

`_init`'s two reads of `harness.workflow` (`cli.py:103,107`) become
`harness.workflows[args.workflow]` — `_init` always resolves exactly the one
name it was given, so indexing by that same name is direct, no iteration.

`--github-workflow` (`cli.py:775`): plan-01 flags this as a candidate
fail-fast check but marks it non-blocking. Design decision: **add it**, since
the cost is one `in` check with data already in hand at the point `_run`
builds `sources` (`cli.py:667`, before `build()` runs) — reject
`args.github_workflow not in served_names` with the same `print
(...); return 2` pattern used for every other `_run` startup error. This
stays inside this task's scope because it's a strict subset of "resolve the
served set" work already being done, not new infrastructure.

## Data schemas

### Port signature changes

```python
# ports/workflows.py
class WorkflowRepository(ABC):
    @abstractmethod
    def get(self, name: str) -> Workflow: ...
    @abstractmethod
    def names(self) -> tuple[str, ...]: ...          # NEW
```

### New driver type

```python
# drivers/fs_workflows.py
class ServedWorkflowRepository(WorkflowRepository):
    def __init__(self, inner: WorkflowRepository, names: Sequence[str]) -> None: ...
    def get(self, name: str) -> Workflow: ...          # WorkflowNotFound if name not served
    def names(self) -> tuple[str, ...]: ...            # returns the served tuple, not inner's
```

### `WorkflowNotFound` message shapes (all pre-existing exception type, new message variants)

| Situation | Message |
|---|---|
| File genuinely missing (unchanged, today) | `workflow 'x' does not exist (<path>)` |
| Broken JSON (unchanged, today) | `workflow 'x' has a broken definition: <json error>` |
| Valid definition, not in the served set (**new**, FR-5) | `workflow 'other' is not served by this harness (served: default, hotfix)` |

The dispatcher needs no change to consume the new variant — `dispatcher.py:60-62`
already catches `WorkflowNotFound` generically and calls `self._fail(task, str(error))`.

### `app.build()` signature

```python
def build(
    root: Path,
    workflows: str | Sequence[str],   # was: workflow_name: str
    *,
    events: EventSink | None = None,
    ...  # every other kwarg unchanged
) -> Harness: ...
```

### `Harness` shape

```python
class Harness:
    workflows: dict[str, Workflow]   # was: workflow: Workflow — insertion order = served order
```

### Event payload — `started`

```jsonc
// before
{"event": "started", "workflow": "default"}

// after
{"event": "started", "workflows": ["default", "hotfix"]}   // sorted
```

Emitted once at `Harness.run()` startup (`app.py:166`); no schema versioning
needed since it is not persisted, only streamed to `EventSink` (stdout log +
projection sink, neither of which parses `started` for routing).

### CLI argument shapes (`run` subcommand only)

| Flag | Before | After |
|---|---|---|
| `--workflow` | single value, default `"default"` | repeatable (`action="append"`); unset ⇒ `["default"]` |
| `--all-workflows` | *(did not exist)* | boolean flag; mutually exclusive with `--workflow` |

`init`/`submit` subcommands' `--workflow`: unchanged (single value, default
`"default"`).

### `BoardProjection` construction shape

```python
BoardProjection(workflows: Sequence[Workflow])   # was: BoardProjection(workflow: Workflow)
```

Every call site — production (`app.py:236`) and the ~23 test call sites —
moves from `BoardProjection(workflow)` to `BoardProjection([workflow])` for
the single-workflow case; multi-workflow tests pass `[wf_a, wf_b]` in served
order.

## Notes carried forward from plan-01, unchanged by this design

- Shared step name across served workflows resolves to one queue / one
  consumer / one behavior, workflow-agnostic (FR-3) — this design doesn't
  introduce a `(workflow, step)` key anywhere; `behavior_for`, `AgentCatalog.get`,
  and `step_queues` all stay keyed by step name alone.
- `maxParallel` interaction (unmerged branch `tsk_2fb79172220a45f3`) is left
  unresolved, as scoped in plan-01 — this design does not touch consumer
  loop cardinality.
