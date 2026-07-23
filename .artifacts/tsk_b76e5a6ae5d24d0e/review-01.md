# Review: Make workflows optional

Reviewed `development-01.md`'s implementation (commit `0faa59a`) against
`plan-02.md`'s FRs, `design-02.md`'s component design, and `architecture-02.md`'s
two required corrections.

## Verified

- **Full test suite**: `.venv/bin/pytest -q` → 514 passed, 1 skipped
  (`HARNESS_SMOKE_CLAUDE`), 0 failed — matches the claimed count.
  `tests/test_architecture.py` (14 tests, all invariants) passes unmodified.
- **Router (`router.py`)**: `route(task, workflow: Workflow | None)` implements
  the 8-row truth table exactly as specified in design-02, byte for byte
  (rows 1/4/6/8 are the pre-existing four branches; rows 2/3/5/7 are new).
  FR-2 (workflow-less move-then-finish), FR-3 (missing edge → `Finished()`
  instead of `Failed`, for a *known* step), FR-4 (unknown status still
  fails; workflow-less task with no/reserved step fails) are all present
  and covered by `tests/test_router.py`'s new cases. Still imports only
  `harness.models`.
- **Dispatcher (`dispatcher.py`)**: `WorkflowRepository.get()` is called
  conditionally on `task.workflow_template is not None`; everything
  downstream is untouched, matching design-02 and preserving invariant 3.
  `tests/test_dispatcher.py`'s FR-3/FR-4 split (`test_missing_edge_finishes_by_default`
  vs. the new `test_unknown_status_lands_in_failed`, using a genuinely
  unrecognized status rather than a missing edge) is exactly the rework
  called for.
- **Architecture-02's two required corrections, both applied**:
  1. `drivers/fs_workflows.py`'s `invalid_step_name` reserves all four board
     names (`END`, `FAILED`, `DONE_COLUMN`, `TODO_COLUMN`), not just
     `end`/`failed` — confirmed in the diff and exercised by
     `tests/test_fs_workflows.py`/`tests/test_cli.py`
     (`test_submit_rejects_a_reserved_step_name`).
  2. `cli.py`'s `_run` resolves the effective `--workflow` default by probing
     `workflows/default.json` (`workflow_name = args.workflow; if None and
     file exists: workflow_name = DEFAULT_WORKFLOW`), restoring the
     `"started"` event's `workflow="default"` field for a plain `harness run`
     on an ordinarily-initialized harness, while a `--no-workflow` harness
     correctly gets `None`. Covered by `test_run_with_no_workflow_harness_defaults_to_none`
     and the plain-run case.
- **Sequencing-critical drivers**: `MemoryWorkflowRepository.names()` and
  `MemoryAgentCatalog.names()` landed in the same commit as the abstract
  `.names()` methods on `ports/workflows.py`/`ports/agent.py` — no
  ABC-instantiation breakage, confirmed by the diff and the green suite.
- **`app.py`**: FR-7's union queue-discovery rule and `Harness.workflow:
  Workflow | None` match design-02's sketch exactly, including the
  best-effort skip of an unparseable workflow file during discovery.
- **`projection.py`**: `column_order`/`BoardProjection` take
  `(steps, workflows=())`, walk every given workflow's edges, and fall back
  to declaration order for an unreached step — matches design-02.
- **`cli.py`**: `submit --step`/`--workflow` mutually exclusive group,
  `init --no-workflow`, `run --github-step`/`--github-workflow` mutually
  exclusive group — all present with matching tests
  (`test_submit_rejects_workflow_and_step_together`,
  `test_init_no_workflow_writes_no_default_workflow`,
  `test_run_github_workflow_and_github_step_are_mutually_exclusive`).
- **`models.py`**: field reorder (`id, created, workflow_template, step, ...`)
  is safe since every construction site uses keyword args (confirmed);
  `to_dict`/`from_dict` widened as specified, backward compatible with
  pre-existing task files (no `step` key → `None`).
- **`drivers/github_source.py`**: optional `step` param, written onto the
  constructed `Task`, matches design-02 (driver itself doesn't re-validate
  mutual exclusivity — deferred to the CLI boundary, as explicitly decided).
- **`api/templates/_task.html`**: FR-9's `{{ task.workflow_template or "—"
  }}` fallback plus new `step` row, matching every other nullable-field row
  on the page.
- **`CLAUDE.md` invariant 8**: reworded exactly as design-02/architecture-02
  specified, landed in this same commit (not deferred).
- All FRs from `plan-02.md` (FR-1 through FR-9) have corresponding
  acceptance-criteria tests, and none of the explicitly out-of-scope items
  (GitHub label-scheme redesign, `--workflow`+`--step` combined use, the
  `extra_steps` escape hatch) were built.

## Issues found

None. No functional requirement is unmet, no architecture invariant is
violated, no required test is missing, and no correctness bug was found in
the diff. The implementation is a faithful, complete realization of
design-02 as corrected by architecture-02.

## Non-binding notes (not blocking)

- `known_steps`/`loadable_workflows` in `app.py`'s `build()` duplicate the
  `workflows.get(name)` call already made once for `workflow_name` when it's
  also present in `workflows.names()` (a second `.get()` on the same file).
  Harmless (idempotent, no I/O-heavy cost, happens once at `build()` time per
  the NFR), but a future cleanup could reuse the already-loaded `workflow`
  instead of re-fetching it inside the discovery loop.

```json
{"outcome": "done", "summary": "Implementation faithfully realizes design-02/architecture-02: router's 8-row truth table, conditional dispatcher workflow lookup, both required architecture-02 corrections (4-name reserved-step guard, run's default-probing fix), FR-7 union queue discovery, projection/CLI/github_source/_task.html changes all verified against source and matching design snippets exactly. Full suite green (514 passed, 1 skipped, 0 failed), test_architecture.py invariants intact, all FR acceptance criteria have corresponding tests. No issues found."}
```
