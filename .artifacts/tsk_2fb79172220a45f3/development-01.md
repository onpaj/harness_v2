# Development — per-step max-parallel-task limits

## Summary

Implemented exactly the four touch points ratified in `architecture-01.md`:
`models.Workflow` gained a `max_parallel` field and `max_parallel_for()`
accessor, `FilesystemWorkflowRepository.get()` parses and validates the new
optional `maxParallel` workflow-JSON key, `Consumer` exposes a read-only
`step` property, and `Harness.run()` spawns `max_parallel_for(consumer.step)`
concurrent `_consumer_loop` coroutines per consumer instead of exactly one.
No other module changed — `dispatcher.py`, `router.py`, `Consumer.tick()`,
`build()`'s wiring, and every driver besides `fs_workflows.py` are untouched,
matching the architecture doc's scope exactly.

## Files changed

- **`src/harness/models.py`** — `Workflow` gains
  `max_parallel: dict[str, int] = field(default_factory=dict)` (a fourth,
  defaulted, trailing field — every existing construction call site keeps
  compiling) and `max_parallel_for(step) -> int` returning
  `self.max_parallel.get(step, 1)`.
- **`src/harness/drivers/fs_workflows.py`** — after transitions are parsed,
  builds a `provisional` `Workflow` to get the known-step set via
  `.steps()` (one source of truth, no re-derivation), then parses the
  optional `maxParallel` object: rejects a non-object value, rejects an
  entry for a step name not in `steps()`, rejects a value that is not a
  positive `int` — with the `isinstance(limit, bool)` check ordered first in
  the `or` chain so `{"review": true}` is rejected rather than silently
  accepted as a limit of 1 (`bool` is an `int` subclass in Python). All
  failures raise `WorkflowNotFound` naming the offending step, consistent
  with how malformed transitions are already rejected. The final `Workflow`
  is then constructed with the validated `max_parallel` dict.
- **`src/harness/consumer.py`** — added a `step` read-only `@property`
  returning `self._step`, directly under the existing `actor` property. Not
  a branch on outcome, imports nothing new — doesn't touch the invariants
  `test_architecture.py` guards.
- **`src/harness/app.py`** — `Harness.run()`'s consumer fan-out changed from
  `*(self._consumer_loop(consumer, poll_interval, stop) for consumer in
  self.consumers)` to a nested loop:
  ```python
  *(
      self._consumer_loop(consumer, poll_interval, stop)
      for consumer in self.consumers
      for _ in range(self.workflow.max_parallel_for(consumer.step))
  ),
  ```
  When `max_parallel` is empty (every workflow written before this feature),
  this produces exactly one coroutine per consumer — byte-for-byte the
  original behaviour. Concurrency safety relies on `TaskQueue.claim()`'s
  atomic rename (a lost race returns `None`, already handled by
  `Consumer.tick()`); no new locking was needed or added.
- **`CLAUDE.md`** — added a "What is responsible for what" bullet next to
  the existing `claim()` entry, describing `Workflow.max_parallel` /
  `max_parallel_for()` as workflow config (not wiring), the default-1
  behaviour, and why `Consumer`/`claim()` needed no other changes.

## Tests added

- **`tests/test_models.py`**:
  - `test_workflow_max_parallel_for_defaults_to_one` — an absent/unknown
    step returns 1.
  - `test_workflow_max_parallel_for_reads_configured_limit` — a configured
    step returns its limit, a sibling step still defaults to 1.
- **`tests/test_fs_workflows.py`**:
  - `test_definition_without_max_parallel_defaults_every_step_to_one` —
    backward compatibility for pre-existing workflow files.
  - `test_max_parallel_is_parsed_and_exposed` — `maxParallel` round-trips
    onto `Workflow.max_parallel_for()`.
  - `test_max_parallel_not_an_object_raises` — non-dict `maxParallel` value.
  - `test_max_parallel_for_unknown_step_raises` — a typo'd step name
    (`"reviw"`) fails loudly instead of silently doing nothing.
  - `test_max_parallel_invalid_value_raises` (parametrized: `0`, `-1`,
    `1.5`, `"3"`, `None`) — every non-positive-int shape is rejected.
  - `test_max_parallel_rejects_bool_even_though_bool_is_an_int_subclass` —
    the one genuinely easy-to-miss correctness detail called out in the
    architecture doc, tested explicitly.
- **`tests/test_app.py`**:
  - `ConcurrencyProbeBehavior` — a `ConsumerBehavior` that records, per
    step (read off `task.status`, which the dispatcher stamps before
    handing the task to a step's queue), the maximum number of concurrent
    `run()` calls observed, using an `asyncio.sleep` hold so concurrent
    calls actually overlap rather than merely completing eventually.
  - `test_max_parallel_bounds_concurrent_runs_per_step` — builds a two-step
    workflow (`plan` → `review` → `end`) with `maxParallel: {"review": 2}`,
    seeds 3 tasks directly into each step's queue, runs the harness with
    the probe behavior wired via `build(..., behavior=probe)`, and asserts
    `probe.max_seen["review"] == 2` while `probe.max_seen["plan"] == 1` in
    the *same* run — the load-bearing regression test for FR-3 identified
    in the plan and architecture docs (proves actual overlap, not just
    eventual completion, and proves the un-overridden step stays serial).

## How to verify

```sh
python3.11 -m venv .venv        # first time only; no venv existed in this worktree
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Result: **485 passed, 1 skipped** (the skipped test is the opt-in
`HARNESS_SMOKE_CLAUDE=1` real-`claude` smoke, unaffected by this change).

To see the feature end-to-end, add to any workflow JSON:
```json
{ "maxParallel": { "review": 3 } }
```
and note that `review`'s consumer will now run up to 3 tasks concurrently
while every other step keeps its default of 1; an invalid or unknown-step
entry raises `WorkflowNotFound` with the offending step name at load time
(surfaces immediately on `harness run`, not as a silent no-op).

## Scope check against plan/design/architecture

Everything in the "in scope" list of `plan-01.md` is implemented (`models`,
`fs_workflows.py`, `app.py`, tests, docs). Everything in "out of scope"
remains untouched: no board/API surfacing of `maxParallel`, no CLI
flag/command, no dispatcher/inbox throttling, no global cross-step cap, no
runtime reconfiguration. `cli.py`'s `DEFAULT_DEFINITION` was left unchanged
per FR-5 (confirmed: no `maxParallel` key was added there).
