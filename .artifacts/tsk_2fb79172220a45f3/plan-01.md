# Per-step max-parallel-task limits

## Summary

Today every workflow step has exactly one `Consumer` whose loop claims and
processes tasks strictly one at a time — an implicit, unconfigurable
concurrency of 1. This feature makes that limit an explicit, per-step
property of the workflow definition (`maxParallel`), defaulting to 1 so
every existing workflow keeps behaving exactly as it does today. An
operator can then raise the limit for a specific step (e.g. `review: 3`) to
let the harness work on several of that step's tasks at once, while the
default keeps single-task-at-a-time semantics everywhere the operator
hasn't opted in.

## Context

Steps whose behaviour is I/O-bound (waiting on `claude -p`, a review agent,
landing a PR) sit idle a lot of wall-clock time relative to CPU/host load.
With one `Consumer` per step ticking strictly sequentially
(`await self._behavior.run(task)` blocks the loop before the next `claim()`),
independent tasks queue up behind each other even when the harness host has
capacity to run several agents concurrently. There is currently no way to
express "let step X work on up to N tasks at once" — concurrency is a fixed
property of the wiring, not the workflow. This feature exposes it as
workflow config.

## Functional requirements

**FR-1 — Workflow definitions can declare a per-step parallelism limit.**
The workflow JSON gains an optional top-level `"maxParallel"` object mapping
step name → positive integer, e.g.:
```json
{
  "name": "default",
  "start": "plan",
  "transitions": [...],
  "maxParallel": { "review": 3 }
}
```
- Acceptance: `FilesystemWorkflowRepository.get()` parses `maxParallel` when
  present and exposes it on the returned `Workflow`.
- Acceptance: a step not mentioned in `maxParallel` (or a workflow with no
  `maxParallel` key at all) defaults to a limit of **1** — byte-for-byte
  identical behaviour to today for every workflow file that predates this
  feature.

**FR-2 — The limit is validated at load time.**
- Acceptance: a `maxParallel` value that is not an integer, or is `< 1`,
  makes `get()` raise `WorkflowNotFound` with a message naming the offending
  step (consistent with how malformed transitions are already rejected).
- Acceptance: a `maxParallel` entry for a step name that does not appear in
  `workflow.steps()` also raises `WorkflowNotFound` (no silent dead config).

**FR-3 — The harness enforces the limit at runtime.**
- Acceptance: for a step with `maxParallel: N`, the harness never has more
  than N calls to that step's `ConsumerBehavior.run(...)` in flight at the
  same time, regardless of how many tasks are waiting in that step's queue.
- Acceptance: for a step with `maxParallel: 1` (explicit or default), at
  most one task is worked on at a time — matching current behaviour exactly.
- Acceptance: raising the limit lets tasks for that step complete out of
  strict FIFO order relative to each other (two tasks legitimately running
  concurrently), which is an accepted, documented change from today's
  strict one-at-a-time processing.

**FR-4 — No change to routing or outcome semantics.**
- Acceptance: `router.route()` and `Dispatcher` are untouched — `maxParallel`
  only throttles how fast a step's queue drains, not where a task goes
  next. Invariants #2 (decision-making split) and #4 (router purity) are
  unaffected.
- Acceptance: the dispatcher's own loop is unaffected — this feature is
  scoped to step consumers, not the dispatcher or the inbox.

**FR-5 — `harness init`'s default workflow is unaffected.**
- Acceptance: `DEFAULT_DEFINITION` in `cli.py` is left without a
  `maxParallel` key (all steps default to 1); operators opt in by hand-
  editing the workflow JSON. No new CLI flags are introduced in this phase.

## Non-functional requirements

- **Concurrency safety:** raising a step's limit above 1 must not let two
  workers claim the same task. This already holds structurally —
  `TaskQueue.claim()` is an atomic rename and returns `None` on a lost
  race — so no new locking is required; the design must simply preserve
  that property (see Rough plan).
- **No busy-loop amplification:** N workers per step each still sleep
  `poll_interval` when they find nothing to claim, so idle CPU cost scales
  with the number of *configured* parallel slots, not with queue depth.
  Document this as an accepted, small per-slot cost (matches the existing
  per-consumer polling cost, just multiplied by N for steps that opt in).
- **Backward compatibility:** every workflow JSON written before this
  change has no `maxParallel` key and must load and run identically to
  today (default limit 1 everywhere).

## Data model

- `Workflow` (models.py) gains a new field:
  `max_parallel: dict[str, int] = field(default_factory=dict)` — sparse map,
  only entries the operator set explicitly.
  Add an accessor `Workflow.max_parallel_for(step: str) -> int` returning
  `self.max_parallel.get(step, 1)`, used everywhere the limit is read so
  "default is 1" lives in exactly one place.
- No changes to `Task`, `HistoryEntry`, `Transition`, or any queue/behavior
  model — this is purely a workflow-level, wiring-time concern; it is not
  per-task state and is not persisted on the task.

## Interfaces

- **Workflow JSON schema** (file-based, `drivers/fs_workflows.py`): new
  optional key `maxParallel: { <step>: <int> }`, sibling to `transitions`.
- **No new HTTP/API endpoints.** `api/` and `projection.py` do not need to
  know about `maxParallel` for this phase (see Open questions re: surfacing
  it on the board later — explicitly deferred).
- **No new CLI subcommands or flags.** Editing the workflow JSON file by
  hand is the only interface, matching how `transitions` are authored today.

## Dependencies and scope

**In scope:**
- `models.Workflow`: new field + accessor.
- `drivers/fs_workflows.py`: parse + validate `maxParallel`.
- `drivers/memory.py` / `MemoryWorkflowRepository` usage in tests: no driver
  change needed (it already stores whatever `Workflow` it's given), but test
  fixtures will construct `Workflow(..., max_parallel={...})` directly.
- `app.py` (`Harness`, `build()`): run `workflow.max_parallel_for(step)`
  concurrent consumer-loop coroutines per step instead of exactly one.
- Tests: `test_fs_workflows.py`, `test_models.py` (if present) or wherever
  `Workflow` is unit-tested, `test_app.py` for the concurrency behaviour.
- Docs: a short note in `CLAUDE.md`'s module map / "what is responsible for
  what" once implemented (per repo convention of documenting invariants and
  responsibilities there) — left to the design/dev steps to phrase.

**Out of scope (explicitly deferred):**
- Surfacing `maxParallel` or current in-flight count on the board / API —
  no UI change in this phase.
- A CLI command to set/edit `maxParallel` — hand-editing the JSON is enough
  for now, consistent with how `transitions` are authored.
- Per-step limits on the **dispatcher** or the **inbox** — only step
  consumers are throttled; the dispatcher continues to process the inbox
  one task per tick as today.
- Global/cross-step concurrency caps (e.g. "at most 5 tasks in flight
  across the whole harness") — this feature is purely per-step.
- Dynamic/runtime reconfiguration of the limit while the harness is
  running — the limit is read once at `build()` time, same lifecycle as
  the rest of the workflow definition.

## Rough plan

1. **Model:** add `max_parallel: dict[str, int]` to `Workflow` plus
   `max_parallel_for(step) -> int` (default 1).
2. **Loader:** in `FilesystemWorkflowRepository.get()`, parse the optional
   `maxParallel` key, validate each value is an `int >= 1` and each key is a
   known step (from the already-computed `steps()`), raise
   `WorkflowNotFound` on violation with a message naming the bad step.
3. **Wiring:** in `Harness.run()`, replace the current
   `*(self._consumer_loop(consumer, poll_interval, stop) for consumer in self.consumers)`
   with, for each consumer, `workflow.max_parallel_for(consumer.step)` copies
   of `_consumer_loop` gathered together. Since `Consumer.tick()` is already
   safe to call concurrently from multiple coroutines (the underlying
   `TaskQueue.claim()` is atomic and a lost race just returns `None`, which
   `Consumer.tick()` already treats as "nothing to do"), no change to
   `Consumer` itself is needed — only how many coroutines drive it.
   Requires exposing the step name off `Consumer` (it already stores
   `self._step`; add a read-only `step` property or pass workflow lookups by
   step key already known in `build()` where consumers are constructed
   1:1 with `step_queues`).
4. **`build()`:** thread `workflow` (already loaded there) through to
   `Harness` construction so `Harness.run()` can call
   `workflow.max_parallel_for(step)` per consumer — `Harness` already stores
   `self.workflow`, so this is just using it in the loop-spawning code
   instead of assuming exactly one loop per consumer.
5. **Tests:**
   - `test_fs_workflows.py`: load a definition with `maxParallel`, assert
     `Workflow.max_parallel_for(...)`; assert invalid value / unknown step
     raises `WorkflowNotFound`; assert a definition without `maxParallel`
     defaults every step to 1.
   - Model-level unit test for `max_parallel_for` default behaviour.
   - `test_app.py`: a concurrency test using `FakeClock` + `MemoryTaskQueue`
     + a scripted behavior that records concurrent-in-flight count (e.g. an
     `asyncio.Event`-gated fake behavior) proving that with `maxParallel: 1`
     only one task runs at a time for a step, and with `maxParallel: 2` two
     tasks can be in flight simultaneously for that step, while another step
     with no override stays at 1. This is the load-bearing regression test
     for FR-3.
6. **Docs:** update `CLAUDE.md`'s "What is responsible for what" /
   `ports/workflows.py` module description to mention `maxParallel` once
   implemented (left for the design/dev steps to word precisely).

## Open questions

- **Should `maxParallel` also apply to the dispatcher/inbox drain rate?**
  Assumed **no** for this phase — the request says "per-step", and the
  dispatcher does no agent work, so throttling it brings no benefit. Scoped
  strictly to step consumers.
- **Should the board/API surface current in-flight vs. limit per step?**
  Useful for observability but not requested; deferred out of scope. Noted
  above as a follow-up.
- **Is a limit of 0 meaningful (a way to "pause" a step)?** Assumed **no** —
  validation requires `>= 1`; pausing a step is a separate, unrelated
  feature (and arguably belongs to `TaskControl`, not the workflow
  definition). If the operator wants this later, treat it as a new request.
- **Fairness across many parallel slots on a busy step:** with N workers per
  step all calling the same FIFO `EnqueueStrategy.select()`, several may
  briefly race for the same earliest task before falling back to the next
  one; only one wins per `claim()`. Assumed acceptable — no fairness
  guarantee is promised today either, and the correctness property (never
  double-claim) is unaffected.
