# Architecture — per-step max-parallel-task limits

## Verdict

The plan and design are correct and I verified every load-bearing claim in
them against the current source (`models.py`, `app.py`, `consumer.py`,
`drivers/fs_workflows.py`, `drivers/memory.py`, `tests/test_architecture.py`,
`tests/test_app.py`, `cli.py`). No factual drift, no missed integration
point. This document ratifies the design as the architecture, resolves the
handful of implementation-level judgment calls the design left open, and
gives the dev step a single unambiguous sequence to follow. **Build exactly
what `design-01.md` describes** — three touch points, no new module, no new
port.

## Alignment with existing patterns and integration points

Verified directly against source, not inferred:

- **`Workflow` is a frozen dataclass with no defaulted fields today**
  (`models.py:145-148`: `name`, `start`, `transitions`, all required,
  positional-compatible). Appending `max_parallel: dict[str, int] =
  field(default_factory=dict)` as a fourth, defaulted, trailing field is the
  only construction-compatible way to extend it — matches the design exactly
  and every existing call site (`FilesystemWorkflowRepository.get()`,
  `MemoryWorkflowRepository` test fixtures, `test_app.py`'s `DEFINITION`)
  keeps compiling untouched.
- **`FilesystemWorkflowRepository.get()`** (`drivers/fs_workflows.py:25-62`)
  already has the exact shape the design assumes: parse, validate, raise
  `WorkflowNotFound(f"workflow {name!r} ...")` per failure mode, construct
  `Workflow` last. Adding a `maxParallel` parse/validate block before the
  final `return Workflow(...)` is a same-shaped addition, not a new pattern.
  `WorkflowRepository.get()`'s contract (`ports/workflows.py`) — "load a
  workflow; if malformed, raise `WorkflowNotFound`" — is unchanged.
- **`Consumer.tick()` is fully synchronous up to the single `await
  self._behavior.run(task)`** (`consumer.py:53-84`). Both `claim()`
  implementations I read — `FilesystemTaskQueue.claim()`
  (`drivers/fs_queue.py:67-76`, `os.replace` + write, no `await`) and
  `MemoryTaskQueue.claim()` (`drivers/memory.py:44-50`, dict mutation, no
  `await`) — run to completion without yielding to the event loop. That is
  the actual proof (not just a plausibility argument) that N coroutines
  calling `tick()` on the *same* `Consumer` cannot interleave mid-`claim()`:
  asyncio is single-threaded and cooperative, so a coroutine that never hits
  an `await` inside `claim()` cannot be pre-empted there. The design's safety
  argument holds structurally, confirmed by reading the actual claim code,
  not assumed.
- **`Harness.run()`'s consumer fan-out** (`app.py:167-171`) is exactly the
  generator-expression shape the design proposes replacing — a one-line
  change from `for consumer in self.consumers` to a nested loop over
  `range(self.workflow.max_parallel_for(consumer.step))`. `self.workflow` is
  already stored (`app.py:106`, set in `__init__`, passed from `build()`
  where it's loaded at `app.py:234`) — no new parameter threading through
  `build()` is needed, confirming plan step 4 is unnecessary busywork; it
  collapses into step 3.
- **`cli.py`'s `DEFAULT_DEFINITION`** (`cli.py:59-71`) has no `maxParallel`
  key today, and this repo's own default workflow is `plan → design →
  architecture → development → review → land → end` — confirming FR-5 by
  inspection: `harness init` ships unaffected, every step defaults to 1.
- **`test_architecture.py`**'s AST-based guard on `consumer.py` (line 175-192,
  "no branch on outcome value") and its import guards on `dispatcher.py`/
  `consumer.py` (lines 41-42, 74, 84, 105-107) are untouched by this feature
  by construction: the only new code in `consumer.py` is a plain `@property`
  returning `self._step`, which is not a branch and imports nothing new.

No component outside `models.py`, `drivers/fs_workflows.py`, `consumer.py`
and `app.py` needs to change. `projection.py`, `api/`, `dispatcher.py`,
`router.py`, `behaviors/`, every driver except `fs_workflows.py`, and
`MemoryWorkflowRepository` are all correctly identified as out of scope.

## Proposed architecture

Four small, mechanically independent edits, in dependency order:

```
models.Workflow                 (adds the field + accessor)
        │
        ▼
drivers/fs_workflows.py         (parses + validates into that field)
        │
        ▼
consumer.Consumer               (exposes .step, read-only)
        │
        ▼
app.Harness.run()               (reads workflow.max_parallel_for(consumer.step)
                                  to decide how many _consumer_loop coroutines
                                  to gather per consumer)
```

### Key decision 1 — where the limit lives: on `Workflow`, not on `Consumer` or in `build()`

**Options considered:**
- (a) Store the limit on `Workflow` (the design's choice).
- (b) Store it on `Consumer` itself, set at construction time in `build()`.
- (c) Keep it as a free-standing `dict[str, int]` threaded separately through
  `build()`/`Harness` alongside `workflow`.

**Chosen: (a).** `maxParallel` is workflow *definition* data — it lives in
the same JSON file as `transitions`, is loaded by the same repository, and
has the same lifecycle (read once at `build()` time, per invariant "no
dynamic reconfiguration"). Putting it anywhere else would split one
conceptual object (the workflow) across two data paths for no benefit. (b)
would also work but adds a second thing `build()` must remember to plumb
into `Consumer.__init__` for zero gain over reading it off `self.workflow`
at loop-spawn time, since `Harness` already carries the whole `Workflow`.
(c) reintroduces exactly the kind of parallel-structure-to-keep-in-sync that
`max_parallel_for()` as a single accessor is designed to avoid.

### Key decision 2 — how concurrency is achieved: N coroutines over one `Consumer`, not N `Consumer` instances

**Options considered:**
- (a) Spawn `max_parallel_for(step)` independent `_consumer_loop` coroutines
  all closing over the *same* `Consumer` instance (the design's choice).
- (b) Construct N separate `Consumer` instances per step in `build()`.
- (c) Give `Consumer` its own internal worker pool / semaphore and a single
  `run()` method replacing `_consumer_loop`.

**Chosen: (a).** This is the only option that requires zero change to
`Consumer`'s or `Dispatcher`'s logic — the concurrency is purely a property
of how many times `Harness.run()` calls `gather()` over the same
`tick()`-driving coroutine, which `test_app.py`'s existing pattern
(`asyncio.create_task(harness.run(...))` + polling) already exercises
correctly for the N=1 case. (b) would work too, but it multiplies
`ConsumerBehavior`/`EnqueueStrategy`/event-sink wiring for no reason — the
components a `Consumer` holds are all stateless or already-shared (strategy,
behavior, clock, events), so duplicating the instance buys nothing that
duplicating the *loop* doesn't already buy, at the cost of more constructor
call sites to keep in sync in `build()`. (c) would centralize the
concurrency logic inside `Consumer`, but it contradicts invariant #2/#3
implicitly by giving `Consumer` a new kind of responsibility (scheduling how
many of itself run) instead of keeping it "a thin wrapper... it decides
nothing" — `Harness.run()` is already the place that decides how many loops
of what run, for the dispatcher and the source pollers alike; per-step fan
out belongs there by the same logic, not inside `Consumer`.

### Key decision 3 — validation granularity: whole-file, at load time, one exception type

Matches how `transitions` are already validated (`fs_workflows.py`): a
single malformed entry fails the *entire* `get()` call with `WorkflowNotFound`,
not a partial/best-effort load. No new exception type. This is not a new
decision so much as a constraint — introducing a second failure mode (e.g. a
warning + silent fallback to 1) would be an inconsistency with how every
other structural error in this file already behaves, and nothing in the
brief asks for graceful degradation here.

## Implementation guidance

**1. `models.py`** — add the field and accessor exactly as designed:

```python
@dataclass(frozen=True)
class Workflow:
    name: str
    start: str
    transitions: tuple[Transition, ...]
    max_parallel: dict[str, int] = field(default_factory=dict)

    def max_parallel_for(self, step: str) -> int:
        return self.max_parallel.get(step, 1)
```

`field` is already imported (`models.py:5`). Place `max_parallel_for` near
`steps()` — both are read-only queries over workflow shape.

**2. `drivers/fs_workflows.py`** — insert validation between the existing
transitions-parsing block and the final `return Workflow(...)`
(`fs_workflows.py:60-62`). Reuse `Workflow(...).steps()` on a provisional
object to get the known-step set rather than re-deriving it by hand — one
source of truth for "what is a step," per `models.py`'s own `steps()`
docstring ("All steps that need a queue"):

```python
provisional = Workflow(name=raw.get("name", name), start=raw["start"], transitions=transitions)
known_steps = set(provisional.steps())

raw_limits = raw.get("maxParallel", {})
if not isinstance(raw_limits, dict):
    raise WorkflowNotFound(
        f"workflow {name!r} has an invalid maxParallel: expected object, "
        f"got {type(raw_limits).__name__}"
    )
max_parallel: dict[str, int] = {}
for step, limit in raw_limits.items():
    if step not in known_steps:
        raise WorkflowNotFound(f"workflow {name!r} has maxParallel for unknown step {step!r}")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise WorkflowNotFound(f"workflow {name!r} has invalid maxParallel for step {step!r}: {limit!r}")
    max_parallel[step] = limit

return Workflow(
    name=raw.get("name", name), start=raw["start"], transitions=transitions,
    max_parallel=max_parallel,
)
```

The `isinstance(limit, bool)` check must come first in that `or` chain
(short-circuit) since `bool` *is* an `int` subclass — this is the one
genuinely easy-to-miss correctness detail in the whole feature; call it out
explicitly in review.

**3. `consumer.py`** — add directly under `__init__`:

```python
@property
def step(self) -> str:
    return self._step
```

**4. `app.py`, `Harness.run()`** — replace the consumer generator expression
at line 169:

```python
*(
    self._consumer_loop(consumer, poll_interval, stop)
    for consumer in self.consumers
    for _ in range(self.workflow.max_parallel_for(consumer.step))
),
```

No change to `build()`, `_consumer_loop`, `Dispatcher`, `router.py`, or any
`ConsumerBehavior`.

**Data flow for a `maxParallel: {"review": 3}` workflow:** `build()` loads
the `Workflow` (now carrying `max_parallel={"review": 3}`) → constructs one
`Consumer` per step as today (`app.py:309-321`, unchanged) → `Harness.run()`
gathers 1 loop each for every step except `review`, where it gathers 3 loops
all driving the *same* `review` `Consumer` → each loop independently calls
`tick()`; at most 3 are ever awaiting `behavior.run()` for `review` at once
because there are only 3 loops; `claim()`'s atomicity (verified above) is
what prevents any of them from double-claiming.

## Risks and mitigations

- **Risk: silent behavior change for existing deployments if the default
  isn't exactly 1.** Mitigated by construction — `max_parallel_for()`'s
  `.get(step, 1)` and the dict's `default_factory=dict` mean an absent key
  is indistinguishable from today's implicit single-loop behavior; the
  loop-count expression produces exactly one coroutine per step when
  `max_parallel` is empty, byte-for-byte the original `gather()` call.
  Verify with a test that asserts `len(consumer_loops_spawned) ==
  len(harness.consumers)` for a `maxParallel`-less workflow — not just that
  outcomes are eventually correct.
- **Risk: the `bool`-is-`int` trap silently accepting `"maxParallel":
  {"review": true}` as a limit of 1.** Mitigated by the explicit
  `isinstance(limit, bool)` guard called out above. Add a dedicated test —
  this is exactly the kind of validation gap that has no compile-time
  signal and only shows up as a confusing runtime surprise.
- **Risk: a step name typo in `maxParallel` (e.g. `"reviw": 3`) silently
  doing nothing** because a sparse dict lookup just falls through to the
  default. Mitigated by FR-2's unknown-step validation, which turns this
  into an unmissable load-time error instead of quiet non-application —
  this is the most important validation in the whole feature; without it,
  `maxParallel` would be the one part of the workflow file that fails
  open instead of closed, which is a real regression in the diagnosability
  this codebase otherwise guarantees for malformed workflow files.
- **Risk: false confidence from a concurrency test that never actually
  overlaps two runs.** A test with `maxParallel: 2` must prove overlap, not
  just eventual completion of two tasks — e.g. a fake behavior that records
  entry/exit timestamps (or increments/decrements a shared counter around an
  `await asyncio.sleep(...)`) and the test asserts the observed
  max-concurrent count reached 2, and separately that a sibling step with no
  override never exceeds 1 in the same run. This is the single test that
  actually validates FR-3; everything else in the plan's test list is
  necessary but not sufficient on its own.
- **Risk: idle-CPU cost from N polling loops on a rarely-busy high-limit
  step.** Already identified and accepted in the plan's non-functional
  requirements (poll_interval sleep cost scales with configured slots, not
  queue depth) — no architectural mitigation needed beyond documenting it,
  which the plan already does.

## Prerequisites before implementation begins

None. This feature has no dependency on unmerged work, no schema migration
(workflow JSON files without `maxParallel` remain valid forever), and no
port or driver interface changes requiring coordination outside this repo.
The dev step can proceed directly from this document and `design-01.md`
using the four edits above, in the order given (model → loader → consumer →
harness), since each step's tests can be written and pass independently
before the next begins.
