# Development: reject a `{"step": X}` Process/Trigger target with no dispatch queue

Implements plan-01.md / design-01.md, folding in both completeness gaps
architecture-01.md flagged (the `test_cli.py`/`test_processes_e2e.py` call
sites and the stale docstrings).

## What changed

**FR-1/FR-2/FR-3 — split `known_targets` into `known_steps`/`known_workflows`**

- `src/harness/drivers/fs_processes.py`
  - `_parse_target(where, target, known_steps, known_workflows)` — a
    `{"step": X}` target is now checked only against `known_steps`, a
    `{"workflow": X}` target only against `known_workflows`. Each is an
    independent `None`-means-skip escape hatch.
  - `compile_process(...)`, `FilesystemProcessRepository.build()`/
    `_build_one()` — signature split, threaded through.
  - `FilesystemProcessAdmin.__init__`/`write()` — gains `known_steps`/
    `known_workflows` kwargs (see FR-4 below); class docstring updated (the
    old text said the served-workflow set was "unlike" `registry`,
    unavailable at admin-write time — no longer true).
- `src/harness/drivers/fs_triggers.py` — mirrored the identical split in
  `FilesystemTriggerRepository.build()`/`_build_one()`/`_parse_target()`.
- `src/harness/app.py`
  - `build()`'s old `known_targets = set(known_steps) | set(resolved)` union
    (with its now-stale explanatory comment) is gone; `process_repo.build()`
    now receives `known_steps=known_steps, known_workflows=set(resolved)`
    directly — no new computation.
  - Added `Harness.known_steps` as a **derived** read-only property
    (`frozenset(self._step_queues)`), not a second stored copy — it can
    never drift from what `Dispatcher.tick` actually routes into.
- `src/harness/cli.py`
  - `_scheduled_sources(...)` signature split the same way; its docstring
    updated.
  - `_run`'s local set computation splits into `known_steps` (the loop over
    served workflows' steps ∪ catalog agent names) and `known_workflows =
    set(served_names)` (no loop needed for that half).
  - `serve()`'s `FilesystemProcessAdmin(...)` construction now passes
    `known_steps=set(harness.known_steps), known_workflows=set(harness.workflows)`
    — closing FR-4 with zero new computation, reusing what `serve()` already
    holds on the built `Harness`.
  - Fixed a stray comment referencing the old `known_targets` name.

**FR-5 — actionable dispatcher failure text**

- `src/harness/dispatcher.py` — `Dispatcher.tick`'s `step {X!r} has no
  queue` failure now appends a remedy: `"(if this step, agent, or its
  process/trigger was added or changed recently, restart the harness
  service to rebuild its queue set)"`. No new dependency, no new method —
  a string-literal change only, keeping the dispatcher ports-only.

**FR-6 — tests** (including the two gaps architecture-01.md found)

- `tests/test_fs_processes.py` / `tests/test_fs_triggers.py`: renamed the
  existing `known_targets=` test to `known_workflows=`; added three new
  cases each — a `{"step": "resolver"}` target where `"resolver"` is a
  served workflow name (rejected), a `{"workflow": "plan"}` target where
  `"plan"` is a step/agent name (rejected), and a valid `{"step": "plan"}`
  target (accepted) — covering FR-1/FR-2's AC1/AC2 as a 2×2 matrix.
- `tests/test_app.py`: added `test_harness_known_steps_matches_the_live_step_queues`
  and `test_build_rejects_a_step_target_naming_a_served_workflow_name` (the
  literal `{"step": "resolver"}`-shaped bug, reproduced at `build()`); fixed
  two stale docstrings referencing `known_targets`.
- `tests/test_fs_process_admin.py`: new "target reachability" section — a
  step target naming a served workflow is rejected, a workflow target naming
  a step is rejected, a reachable step target is accepted and round-trips,
  and the no-snapshot-wired case stays lenient (matching every other
  `known_*=None` escape hatch).
- `tests/test_cli.py`: renamed all 8 `_compile_processes(..., known_targets=...)`
  call sites to `known_workflows=...` (mechanical, all were workflow-only
  targets); added `known_steps`/`workflows` attributes to the four
  `FakeHarness` test doubles `serve()` now reads; added
  `test_serve_wires_known_steps_and_workflows_into_the_process_admin`
  proving the FR-4 wiring end-to-end through `serve()`.
- `tests/test_processes_e2e.py`: renamed all 3 `known_targets=` call sites
  to `known_workflows=` (mechanical, all workflow-only targets).
- `tests/test_dispatcher.py`: extended `test_step_without_queue_lands_in_failed`
  to also assert `"restart"` is present in the failure reason, keeping the
  existing `"missing" in reason` assertion.

**ADR**

- `docs/adr/0022-validate-targets-against-the-real-dispatch-surface.md` —
  records the `known_steps`/`known_workflows` split as the reusable
  principle ("validate a target against the exact set the runtime
  dispatches into, never a broader superset merging two namespaces"),
  covering both the `nanoclaw-sweep` incident's live-edit/restart-ordering
  path and the `{"step": "resolver"}` latent sibling bug found during
  investigation, and explicitly notes the residual risk that FR-1–FR-4
  don't prevent the literal `nanoclaw-sweep` incident — only FR-5 makes it
  legible.

## Verify

```sh
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Full suite: 1522 passed, 1 skipped (pre-existing opt-in `HARNESS_SMOKE_CLAUDE`
skip), 0 failed.

Targeted re-runs during development:
- `.venv/bin/pytest -q tests/test_fs_processes.py tests/test_fs_triggers.py tests/test_app.py tests/test_fs_process_admin.py tests/test_cli.py tests/test_processes_e2e.py tests/test_dispatcher.py tests/test_architecture.py tests/test_adr_docs.py`

`grep -rn known_targets src/harness tests` returns no remaining production or
test call sites (one intentional docstring-prose mention in
`tests/test_app.py` describing the *old*, now-fixed behavior).

```json
{"outcome": "done", "summary": "Implemented the known_steps/known_workflows split across fs_processes.py, fs_triggers.py, app.py (plus a new derived Harness.known_steps property) and cli.py (FR-1-4), reworded the dispatcher's step-has-no-queue failure to name a concrete restart remedy (FR-5), added ADR-0022, and wrote/updated regression tests across 8 test files including the two gaps architecture-01.md flagged (test_cli.py's 8 _compile_processes call sites, test_processes_e2e.py's 3 direct build() call sites, and the stale FilesystemProcessAdmin/cli.py docstrings). Full suite: 1522 passed, 1 skipped, 0 failed."}
```
