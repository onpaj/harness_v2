# Review — per-step max-parallel-task limits

## Verdict

**done.** The implementation matches `design-01.md`/`architecture-01.md` exactly —
four touch points, no new module, no new port — and satisfies every functional
requirement in `plan-01.md`.

## What I checked

- **Diff vs. architecture doc** (`git show 23bfd37`): `models.py`,
  `drivers/fs_workflows.py`, `consumer.py`, `app.py` changed exactly as
  specified, line for line matching the code blocks in `architecture-01.md`.
  No other production module touched (`dispatcher.py`, `router.py`,
  `behaviors/`, `projection.py`, `api/`, other drivers all untouched).
- **FR-1** (declare limit in workflow JSON): `Workflow.max_parallel` +
  `max_parallel_for()` added as a defaulted trailing field — every existing
  `Workflow(...)` call site still compiles. Confirmed via full test run.
- **FR-2** (validation at load time): `FilesystemWorkflowRepository.get()`
  rejects a non-object `maxParallel`, an entry for an unknown step, and any
  non-positive-int value — all via `WorkflowNotFound`, consistent with how
  malformed `transitions` already fail. The `bool`-before-`int` ordering trap
  flagged in the architecture doc is handled correctly
  (`isinstance(limit, bool) or not isinstance(limit, int) or limit < 1`) and
  has a dedicated test (`test_max_parallel_rejects_bool_even_though_bool_is_an_int_subclass`).
- **FR-3** (runtime enforcement): `Harness.run()`'s fan-out now spawns
  `max_parallel_for(consumer.step)` coroutines per consumer via a nested
  generator expression. The regression test
  (`test_max_parallel_bounds_concurrent_runs_per_step`) is the load-bearing
  one here — it uses a probe behavior with an `asyncio.sleep` hold to prove
  actual overlap (`max_seen["review"] == 2`) rather than just eventual
  completion, and simultaneously proves the un-overridden `plan` step stays
  serial (`max_seen["plan"] == 1`) in the same run. This is exactly the kind
  of test the architecture doc called out as necessary but easy to fake.
- **FR-4** (no routing/outcome semantic change): confirmed by inspection —
  `dispatcher.py` and `router.py` have zero diff. `consumer.py`'s only change
  is a read-only `@property` returning `self._step`; it adds no branch on
  outcome and imports nothing new, so `test_architecture.py`'s AST guards
  stay satisfied (verified: `pytest tests/test_architecture.py -q` → 14
  passed).
- **FR-5** (`harness init` default workflow unaffected): `cli.py`'s
  `DEFAULT_DEFINITION` has no `maxParallel` key (confirmed unchanged in the
  diff), and `max_parallel_for()`'s `.get(step, 1)` default means an absent
  `maxParallel` produces byte-for-byte the original single-loop-per-consumer
  fan-out.
- **Backward compatibility**: `test_definition_without_max_parallel_defaults_every_step_to_one`
  directly covers every pre-existing workflow file.
- **Full suite**: ran `.venv/bin/pytest -q` myself → **485 passed, 1
  skipped** (the opt-in real-`claude` smoke), matching what development-01.md
  reports. No test was weakened or removed to make this pass.
- **Invariants (`CLAUDE.md`)**: none of the 23 listed invariants are touched
  by this feature — concurrency is achieved purely by how many times
  `Harness.run()` calls `gather()` over the same `Consumer.tick()`, which
  invariant #2/#3 (consumer/dispatcher decision boundary) is unaffected by.
  `claim()`'s atomic rename (already relied upon, not modified) is what
  prevents double-claiming across the fanned-out loops — verified this
  argument against the actual `claim()` implementations
  (`drivers/fs_queue.py`, `drivers/memory.py`), both fully synchronous up to
  the point of claiming, so no interleaving is possible mid-claim.
- **Docs**: the `CLAUDE.md` addition under "What is responsible for what" is
  accurate and consistent with the shipped code.

## Non-binding observations (not blocking)

- `drivers/fs_workflows.py` now constructs `Workflow(...)` twice (once
  provisional to get `known_steps`, once final). This is a deliberate,
  documented choice (reuse `steps()` as the one source of truth for "what is
  a step" rather than re-deriving it by hand) and is cheap — not worth
  changing.
- No board/API surfacing of `maxParallel` and no CLI flag were added — both
  correctly out of scope per the plan.

## Conclusion

Scope, correctness, tests, and invariant-preservation all check out against
the spec/design/architecture chain. No changes requested.
