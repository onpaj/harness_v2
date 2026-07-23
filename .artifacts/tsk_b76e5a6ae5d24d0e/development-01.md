# Development: Make workflows optional

Implements `design-02.md` as corrected by `architecture-02.md` (both required
corrections applied): the reserved-step-name guard covers all four board
names (`end`, `failed`, `done`, `todo`), and `harness run`'s `--workflow`
default is resolved by probing for `workflows/default.json` rather than
unconditionally defaulting to `None`.

## Summary

An agent/queue is now a complete unit of work by default. `harness submit
--step <name>` (or `GithubTaskSource(step=...)`) runs a single step and
finishes — no workflow file required. A workflow, when present, only adds
redirections on top: an edge it defines behaves exactly as before, and a
known step with no outgoing edge for the outcome now finishes by default
instead of failing (only a genuinely *unknown* step still fails loudly).
`harness init --no-workflow` creates a workflow-less harness; queue discovery
is now the union of every workflow file's steps and every catalog agent, not
one mandatory workflow's `steps()`.

## Files changed

Sequenced per `design-02.md`'s binding order (ports/drivers `.names()` first,
to avoid the `MemoryAgentCatalog`/`MemoryWorkflowRepository` abstract-method
instantiation blocker):

- **`src/harness/ports/workflows.py`** — new abstract `WorkflowRepository.names()`.
- **`src/harness/ports/agent.py`** — new abstract `AgentCatalog.names()`.
- **`src/harness/drivers/fs_workflows.py`** — `FilesystemWorkflowRepository.names()`
  (globs `*.json`, filters invalid names); new `invalid_step_name()` reserving
  `end`/`failed` (`models.py`) **and** `done`/`todo` (`ports/board.py`) — the
  architecture-02 correction, since a step name now reaches the router from
  `--step`/a `TaskSource`, not only from inside a trusted workflow file.
- **`src/harness/drivers/fs_agents.py`** — `FilesystemAgentCatalog.names()`.
- **`src/harness/drivers/memory.py`** — `.names()` on `MemoryWorkflowRepository`
  and `MemoryAgentCatalog`, landed in this same commit so no existing
  in-memory-driver call site breaks once the abstract methods exist.
- **`src/harness/models.py`** — `Task.workflow_template: str | None = None`,
  new `Task.step: str | None = None`; `id`/`created` moved ahead of
  `workflow_template` (every construction site already uses keyword args);
  `to_dict`/`from_dict` widened (`raw.get(...)` instead of required keys),
  serialized key `step` added.
- **`src/harness/router.py`** — `route(task, workflow: Workflow | None)`, the
  8-row truth table: workflow-less fresh task → `MoveTo(task.step)` (row 2);
  no usable step (`None`/reserved) → `Failed` (row 3); workflow-less task with
  an outcome → `Finished()` unconditionally (row 5); a *known* step with no
  edge for the outcome → `Finished()` instead of `Failed` (row 7, FR-3); an
  *unknown* step still → `Failed` (row 6, FR-4 preserved). Still imports only
  `harness.models` (`test_router_only_knows_models` unmodified).
- **`src/harness/dispatcher.py`** — `WorkflowRepository.get()` is only called
  when `task.workflow_template is not None`; `None` flows straight into
  `route()` otherwise. Everything downstream (`_move`/`_finish`/`_fail`,
  event emission) is untouched.
- **`src/harness/app.py`** — `build(root, workflow_name: str | None = None,
  ...)`; queue discovery is now the union of every step across every loadable
  workflow file (`workflows.names()` → `.get(name).steps()`, best-effort —
  an unparseable file is skipped, not fatal) and, when a catalog is wired,
  every agent name (`catalog.names()`). `Harness.workflow: Workflow | None`;
  the `"started"` event's `workflow` field is `None` when no workflow is
  named. `BoardProjection` is now seeded with `(steps, workflows)`.
- **`src/harness/projection.py`** — `column_order(steps, workflows=())` /
  `BoardProjection.__init__(steps, workflows=())`: the reachability walk now
  starts from every given workflow's `start` (in the order given), and a step
  the walk never reaches falls back to declaration order — still landing
  between `todo` and `done`.
- **`src/harness/cli.py`**:
  - `submit` gains `--step`, mutually exclusive with `--workflow`
    (`add_mutually_exclusive_group()`, new pattern in this file). Neither
    flag given still defaults to the `default` workflow (unchanged
    behavior). `--step` is checked against `invalid_step_name` at this
    boundary before a task file is ever written.
  - `init` gains `--no-workflow`: writes `agents/`, `repos.json` and (new,
    needed for `submit` to accept a workflow-less task afterward)
    `tasks/`, then returns — no `workflows/default.json`, no `build()`
    call. Plain `init` is unchanged.
  - `run` gains `--github-step`, mutually exclusive with
    `--github-workflow`, mirroring `submit`/`_github_sources`.
  - `run`'s `--workflow` argparse default is `None` (needed so an omitted
    flag can mean "no workflow" for a `--no-workflow` harness), but `_run`
    resolves the *effective* default by probing for
    `workflows/default.json` — the architecture-02 correction, so plain
    `harness run` (no flags) on an ordinarily-initialized harness still
    reports `workflow="default"` in the `"started"` event exactly as
    before this change.
- **`src/harness/drivers/github_source.py`** — `GithubTaskSource.__init__`
  gains `step: str | None = None`; `poll()` writes `step` onto the
  constructed `Task`. The driver itself doesn't validate mutual exclusivity
  (the CLI boundary already does).
- **`src/harness/api/templates/_task.html`** — the `workflow` row gets the
  same `{{ x or "—" }}` fallback every other nullable field already has (FR-9,
  it previously rendered a literal `None`); new `step` row below it.
- **`CLAUDE.md`** — invariant 8 reworded: routing decides "solely on
  `(status, lastOutcome)`, and — since workflows became optional — whether a
  workflow is present at all; never on `repository`/`worktree`, `step`, or
  `data`."

## Tests

Updated for the new signatures, plus new coverage per FR-1–FR-9:

- `tests/test_router.py` — new rows: workflow-less fresh task, finish after
  one outcome, no-step / reserved-step failure, still-requires-lastOutcome;
  `test_missing_edge_fails` → `test_missing_edge_finishes_by_default` (FR-3).
- `tests/test_dispatcher.py` — workflow-less fresh-task routing and
  finish-after-one-pass; `test_missing_edge_lands_in_failed` reworked into
  `test_missing_edge_finishes_by_default` (FR-3) plus a new
  `test_unknown_status_lands_in_failed` (FR-4, using a genuinely unrecognized
  status instead of a missing edge).
- `tests/test_models.py` — `step` round-trips through JSON; `from_dict`
  defaults both `step` and `workflowTemplate` when absent (backward
  compatibility with pre-existing task files).
- `tests/test_fs_workflows.py` / `tests/test_fs_agents.py` /
  `tests/test_memory_drivers.py` / `tests/test_agent_ports.py` — `.names()`
  on all four concrete repositories; `invalid_step_name` rejects all four
  reserved board names plus the existing shape checks.
- `tests/test_projection.py` — `column_order`/`BoardProjection`'s new
  `(steps, workflows)` signature across every existing test, plus two new
  cases: fallback-to-declaration-order for an unreachable step, and a
  no-workflow board.
- `tests/test_app.py` — `build()` without a workflow name (`harness.workflow
  is None`); FR-7's union-of-workflows-and-catalog discovery with no
  duplicates; confirms the one-workflow/no-catalog case is byte-for-byte
  unchanged.
- `tests/test_cli.py` — `submit --step` writes a workflow-less task;
  `--workflow`/`--step` are mutually exclusive; `--step end` is rejected at
  the CLI boundary; `init --no-workflow` writes no default workflow but does
  leave the harness `submit`-able; `--github-workflow`/`--github-step`
  mutual exclusivity and defaulting; `run`'s effective-workflow resolution
  (`"default"` when the file exists, `None` for a `--no-workflow` harness).
- `tests/test_github_source.py` — `step` param produces a workflow-less task.
- `tests/test_api_html.py` — the task-detail fragment never renders a
  literal `None` for either the pipeline (`workflow` set, `step` absent) or
  workflow-less (`workflow` absent, `step` set) case.
- `tests/test_phase3_e2e.py` — new end-to-end case: a workflow-less task
  (real git worktree, fake agent runner, `MemoryForge`) reaches `done/`
  after exactly one step, with no `workflows/*.json` on disk and no `land`
  step involved.

## Verify

```sh
.venv/bin/pytest -q
```

514 passed, 1 skipped (the opt-in `HARNESS_SMOKE_CLAUDE` smoke), 0 failed —
up from the pre-change baseline of 472 passed / 1 skipped. `test_architecture.py`
(14 tests, all invariants) passes unmodified.

Manual smoke of the new CLI surface:

```sh
harness init --root /tmp/wf-less --no-workflow
echo '{"prompt": "You are the triage agent."}' > /tmp/wf-less/agents/triage.json
harness submit --root /tmp/wf-less --step triage   # writes a task, prints its id
harness submit --root /tmp/wf-less --step end       # error: invalid step name: 'end'
harness submit --root /tmp/wf-less --workflow default --step triage  # argparse error, exit 2
```
