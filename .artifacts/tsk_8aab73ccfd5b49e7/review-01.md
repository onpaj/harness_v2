# Review: known_steps/known_workflows split for Process/Trigger targets

## Verdict: done

## What I checked

Read plan-01.md, design-01.md, architecture-01.md, development-01.md, then
diffed the actual implementation commit (`153ba91`) against the design and
re-ran the full suite independently (`python3.11 -m venv .venv && pip install
-e ".[dev]"` then `.venv/bin/pytest -q`): **1522 passed, 1 skipped, 0
failed**, matching development-01.md's/verify-01.md's claim.

## Conformance to spec/design

- **FR-1/FR-2/FR-3** — `_parse_target` in both `drivers/fs_processes.py` and
  `drivers/fs_triggers.py` now takes independent `known_steps`/
  `known_workflows` and checks each target key only against its own
  namespace, exactly as design §1.1 specifies. Every call site
  (`compile_process`, `FilesystemProcessRepository.build`/`_build_one`,
  `FilesystemTriggerRepository.build`/`_build_one`, `app.py`'s `build()`,
  `cli.py`'s `_scheduled_sources`/`_run`) threads the split through — `grep
  -rn known_targets src/harness tests` finds only one intentional docstring
  mention of the old (fixed) behavior in `tests/test_app.py`, no production
  or live test call site.
- **FR-4** — `Harness.known_steps` is a derived read-only property
  (`frozenset(self._step_queues)`), not a duplicated/stored set — verified
  at `app.py`. `FilesystemProcessAdmin` gains the two kwargs, `serve()`
  wires `known_steps=set(harness.known_steps), known_workflows=set(harness.workflows)`,
  and the class docstring was updated to no longer contradict the new
  reality (architecture-01.md §3.2's flagged gap is closed).
- **FR-5** — `dispatcher.py`'s only change is the literal reason string
  passed to `self._fail(...)`; no new import, no new dependency. Dispatcher
  stays ports-only (confirmed no diff to its import block), consistent with
  CLAUDE.md invariant #33/#43 and the plan's explicit constraint.
- **FR-6** — Regression tests land in exactly the files architecture-01.md
  required, including the two completeness gaps it flagged:
  `tests/test_cli.py` (8 `_compile_processes` call sites renamed,
  `FakeHarness` doubles gain `known_steps`/`workflows`, plus a new
  `test_serve_wires_known_steps_and_workflows_into_the_process_admin`
  end-to-end test) and `tests/test_processes_e2e.py` (3 call sites renamed).
  `tests/test_fs_processes.py`/`test_fs_triggers.py` each add the 2×2
  step-vs-workflow rejection/acceptance matrix. `tests/test_app.py` adds a
  `known_steps`-matches-`step_queues` test and a `build()`-level repro of the
  literal `{"step": "resolver"}` bug. `tests/test_fs_process_admin.py` adds
  the admin-side reachability matrix plus the no-snapshot-wired lenient case.
  `tests/test_dispatcher.py` extends the existing test to also assert
  `"restart"` in the reason, keeping the original `"missing"` assertion.

## Correctness

- No logic errors found; the `_parse_target` split correctly rejects a
  step-named-as-workflow and workflow-named-as-step in both directions,
  confirmed by reading the diff and the new tests.
- `known_steps=None`/`known_workflows=None` remain independent escape
  hatches, matching the existing `known_repositories=None` idiom — verified
  no accidental coupling between the two checks.
- No test regressions; full suite passes independently reproduced.
- ADR-0022 is well-formed (`Status`, `## Context`, `## Decision`,
  `## Consequences`), accurately documents the residual risk (FR-1–FR-4
  don't prevent the literal `nanoclaw-sweep` restart-ordering incident —
  only FR-5 makes it legible), matching architecture-01.md §5's explicitly
  accepted scoping.

No functional requirement is unmet, no architecture/invariant violation, no
missing required test, no correctness bug found.

```json
{"outcome": "done", "summary": "Implementation matches plan-01.md/design-01.md exactly, including both completeness gaps architecture-01.md flagged (test_cli.py's 8 call sites, test_processes_e2e.py's 3 call sites, and the stale FilesystemProcessAdmin/cli.py docstrings, all fixed). known_steps/known_workflows split is correctly independent in both directions, Harness.known_steps is properly derived (not duplicated) from _step_queues, dispatcher.py stays ports-only with only a string-literal change, and ADR-0022 accurately documents the residual restart-ordering risk. Re-ran the full suite independently: 1522 passed, 1 skipped, 0 failed."}
```
