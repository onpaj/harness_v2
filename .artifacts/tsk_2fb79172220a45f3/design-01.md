# Design — per-step max-parallel-task limits

No UI section: this feature has no user-facing interface. The only surface is
a workflow JSON file the operator hand-edits (as they already do for
`transitions`); the board/API are explicitly unaffected (plan FR-5, out of
scope list).

## Component design

Three existing components change; nothing new is added.

### 1. `models.Workflow` — carries the limit

```python
@dataclass(frozen=True)
class Workflow:
    name: str
    start: str
    transitions: tuple[Transition, ...]
    max_parallel: dict[str, int] = field(default_factory=dict)

    def max_parallel_for(self, step: str) -> int:
        """Configured limit for a step, or 1 if the operator didn't set one."""
        return self.max_parallel.get(step, 1)
```

Placed as a new trailing field with a default, so every existing call site
that constructs `Workflow(name=..., start=..., transitions=...)` positionally
or by keyword keeps compiling unchanged (tests, `MemoryWorkflowRepository`
fixtures, `FilesystemWorkflowRepository`). `steps()` is untouched — it still
derives the queue set purely from `transitions`/`start`; `max_parallel` never
introduces a step that isn't already reachable by an edge, which is exactly
what loader validation (below) enforces.

`max_parallel_for` is the single place "default is 1" lives, per plan FR-1.
Every reader (today: only `Harness.run()`) calls the accessor, never the
dict directly.

### 2. `FilesystemWorkflowRepository.get()` — parses and validates

Responsibility: turn the optional `"maxParallel"` JSON object into the
validated `dict[str, int]` the model stores, or raise `WorkflowNotFound` —
the same failure type and file-line style already used for a bad `start` or
a malformed transition, so a broken workflow file fails in one consistent
way regardless of which part is broken.

Sequencing inside `get()`:

1. Parse `transitions` as today (unchanged).
2. Build the step set the same way `Workflow.steps()` would (needed to
   validate `maxParallel` keys before the final `Workflow` exists — cheapest
   way is to construct a provisional `Workflow(name=..., start=..., transitions=transitions)`
   and call `.steps()` on it, then discard it in favor of the final object
   built in step 4).
3. Read `raw.get("maxParallel", {})`:
   - Not a dict → `WorkflowNotFound(f"workflow {name!r} has an invalid maxParallel: expected object, got {type(...).__name__}")`.
   - Per entry `step -> limit`:
     - `step` not in the step set from (2) → `WorkflowNotFound(f"workflow {name!r} has maxParallel for unknown step {step!r}")`.
     - `limit` not an `int` (excluding `bool`, since `bool` is an `int`
       subclass in Python and `True`/`False` must not silently parse as
       `1`/`0`) or `limit < 1` → `WorkflowNotFound(f"workflow {name!r} has invalid maxParallel for step {step!r}: {limit!r}")`.
4. Construct the final `Workflow(name=..., start=..., transitions=transitions, max_parallel=validated)`.

This keeps `WorkflowRepository`'s contract exactly as documented in
`ports/workflows.py` ("load a workflow; if malformed, raise
`WorkflowNotFound`") — no new exception type, no change to the port
interface. `MemoryWorkflowRepository` (test driver) needs no change: it
stores whatever `Workflow` it's handed, so tests that want a non-default
limit construct `Workflow(..., max_parallel={"review": 3})` directly and
skip file parsing entirely.

### 3. `Consumer` — exposes its step name

Add a read-only property so the harness can look up each consumer's limit
without reaching into a private attribute:

```python
@property
def step(self) -> str:
    return self._step
```

No other change to `Consumer`. `tick()` is already safe to invoke from
multiple coroutines concurrently: `EnqueueStrategy.select()` reads a
snapshot of `queue.list()`, and `TaskQueue.claim()` is the atomic `rename`
described in invariant "each queue has its own `.processing/`" — a second
coroutine racing for the same task gets `None` back and `tick()` reports
"nothing to do," which is already handled. Two coroutines running the same
`Consumer` therefore behave exactly like two independent consumers sharing a
queue; the design leans on that existing correctness property rather than
adding new synchronization.

### 4. `Harness.run()` — spawns N loops per step instead of 1

Today:

```python
*(self._consumer_loop(consumer, poll_interval, stop) for consumer in self.consumers)
```

Becomes:

```python
*(
    self._consumer_loop(consumer, poll_interval, stop)
    for consumer in self.consumers
    for _ in range(self.workflow.max_parallel_for(consumer.step))
)
```

`self.workflow` is already stored on `Harness` (set in `__init__`, loaded in
`build()` before consumers are constructed), so no new parameter threading
is needed — `build()` is unchanged. `_consumer_loop` itself is unchanged: it
is already just "call `tick()` in a loop, sleep `poll_interval` on a miss."
Running it N times concurrently for the same `Consumer` instance is exactly
the mechanism FR-3 asks for: N independent polling loops, each capable of
claiming and running one task at a time, sharing one queue and one
behavior, bounded at N in flight because there are only N loops.

A step with the default limit (1, no `maxParallel` entry) gets exactly one
loop — byte-for-byte the same coroutine set `asyncio.gather` receives today,
satisfying FR-1's backward-compatibility acceptance criterion structurally,
not just behaviorally.

No change to `Dispatcher`, `router.route()`, `build()`'s wiring of
behaviors, or `ConsumerBehavior` — matches FR-4 (routing/outcome semantics
untouched) and invariants #2/#4.

## Data schemas

### Workflow JSON file (`drivers/fs_workflows.py` input)

New optional top-level key, sibling to `transitions`:

```json
{
  "name": "default",
  "start": "plan",
  "transitions": [
    {"from": "plan", "on": "done", "to": "review"},
    {"from": "review", "on": "done", "to": "land"},
    {"from": "review", "on": "request_changes", "to": "plan"},
    {"from": "land", "on": "done", "to": "end"}
  ],
  "maxParallel": {
    "review": 3
  }
}
```

- Type: object, keys are step names (strings), values are integers `>= 1`.
- Omitted entirely, or a step omitted from it → that step's limit is 1.
- A key naming a step not present in the workflow's derived step set, or a
  non-integer/`< 1` value → the whole workflow fails to load
  (`WorkflowNotFound`), same failure granularity as a bad transition today
  (the file is all-or-nothing, not partially applied).

### `Workflow` dataclass (`models.py`)

| Field | Type | Notes |
|---|---|---|
| `max_parallel` | `dict[str, int]` | Sparse — only steps the operator explicitly overrode. Default `{}`. |

New method: `max_parallel_for(step: str) -> int` — `self.max_parallel.get(step, 1)`.

No changes to `Task`, `HistoryEntry`, `Transition`, `BehaviorResult`, event
payloads, or any API/board response shape — this is load-time workflow
config, not per-task state, and is never serialized onto a task or emitted
in an event.

## Cross-cutting notes carried from the plan

- `test_architecture.py`'s guard that `dispatcher.py`/`consumer.py` import
  only ports is unaffected — the new `Consumer.step` property returns a
  plain `str`, no new imports.
- The two invariants this feature could plausibly threaten both hold by
  construction: invariant #4 (router purity) — `router.py` is untouched;
  invariant #2 (decision-making split) — `Harness.run()`'s loop-count
  computation is a wiring-time fan-out, not a decision about outcome
  routing, so it doesn't belong to and doesn't touch the dispatcher/router
  split.
