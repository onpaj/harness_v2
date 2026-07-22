# Plan: Make workflows optional

## Summary

Today every task is bound to exactly one `Workflow` (a JSON file of explicit
`from → on → to` edges), and the harness process itself is wired to exactly one
workflow at `build()` time. This task inverts the default: **a single
agent/queue is a complete unit of work on its own** — submit a task straight at
a step, the step's agent runs once, and the task finishes. A **workflow becomes
optional and purely additive**: when a task carries one, its edges *redirect*
the task onward past a step instead of letting it finish there; where the
workflow is silent, the default (finish) still applies. Multi-step pipelines
(`plan → design → … → land`) keep working exactly as they do today — this is a
generalization, not a replacement.

## Context

The current model forces every task through a fully-specified pipeline, even
when the actual need is "run one agent against this repo and open a PR" or
"run one review and report back" — cases that don't need a `land`/`review`
chain at all. Operators currently work around this by writing a trivial
one-step workflow file per use case. That's boilerplate for something that
should be the default shape, and it also means a single `harness` process is
pinned to one pipeline definition, so ad hoc single-step jobs can't run
alongside a real pipeline. Loosening this is a natural next step after phase 4
(GitHub source): sources like a `harness:todo` issue label or an interactive
`harness submit` should be able to say "just run `development`" without an
operator hand-authoring a matching workflow first.

## Functional requirements

**FR-1 — A task can target a step directly, without a workflow.**
`harness submit` accepts a step name in place of a workflow name (e.g.
`--step development` as an alternative to `--workflow default`). The resulting
task carries no workflow reference. The same applies at the `TaskSource` layer
(`GithubTaskSource` and any future source) — a source configuration may name a
step instead of a workflow.
*Acceptance:* `harness submit --step development ...` writes a task with
`workflow_template: null` (or absent) and reaches the `development` queue.

**FR-2 — A workflow-less task is routed straight to `end` after one pass.**
A fresh task with no workflow enters the queue named on the task; once that
step's agent returns any outcome, the dispatcher finishes the task (`end`,
i.e. the `done` queue) without requiring any further configuration.
*Acceptance:* `route(task, workflow=None)` with `status=None` yields
`MoveTo(task's step)`; with `status=<step>` and any outcome, yields
`Finished()`. An end-to-end run (single agent, `DONE`) lands the task in
`done/` with exactly one history hop out and one back.

**FR-3 — A workflow only adds redirections; it is never required to be
exhaustive.** For a task that does carry a workflow, an edge that exists for
`(status, outcome)` behaves exactly as today (`MoveTo`/`Finished` at an
explicit `end` edge). For a `(status, outcome)` pair the workflow does **not**
cover, the task finishes by default — the same default as FR-2 — rather than
failing. A workflow's job shrinks to "list the redirects"; anywhere it's
silent, the step it's silent about is a complete unit of work.
*Acceptance:* a workflow that defines only `plan --done--> design` (no edge
for `design`'s `done` outcome) sends a task that finishes `design` straight to
`end`, not to `failed/`.

**FR-4 — Corruption/misconfiguration still fails loudly, for tasks that do
carry a workflow.** If a task's `status` is not one of the workflow's known
steps at all (typo, stale definition, hand-edited file), routing still fails
into `failed/` with a clear reason — FR-3's new default only fires for a
*known* step with no outgoing edge, not for an unrecognized one. (For a
workflow-less task there is no workflow to check membership against, so this
check does not apply there — see Open Questions for the accepted trade-off.)
*Acceptance:* existing `test_unknown_status_fails`-style behavior is
preserved when a workflow is present; `test_missing_edge_fails`-style behavior
changes to `Finished` per FR-3.

**FR-5 — Multi-step pipelines are unaffected.** The built-in `default`
workflow (`plan → design → architecture → development → review → land`) and
any other hand-authored multi-edge workflow keep behaving exactly as they do
today, edge for edge. This is verified by the full existing e2e/smoke suite
without modification beyond the router/dispatcher tests called out in FR-3/4.

**FR-6 — `harness init` does not require a workflow to produce a usable
harness.** An operator can initialize a harness with one or more standalone
agents and no workflow file at all, and still `submit`/`run` against them.
Initializing with `--workflow` continues to work unchanged for pipeline setups.
*Acceptance:* `harness init --root X` (no `--workflow`) succeeds, and
`harness submit --root X --step <agent>` on an agent created afterwards
reaches that queue.

**FR-7 — The set of live queues is no longer derived from a single
workflow's `steps()`.** Today `build()` creates one `TaskQueue` per step of
the *one* workflow it's given. Since a harness process must now be able to run
workflow-less tasks alongside pipeline tasks — potentially against several
different workflows — the set of step queues must be derived from the
declared agents (`agents/*.json`) rather than from one workflow's edge list.
See Open Questions for the exact discovery mechanism.

**FR-8 — The board still renders correctly.** `todo` → single column → `done`
for a workflow-less task; the existing column-ordering-by-reachability keeps
working for pipeline tasks. Column order can no longer assume a single
`Workflow.start` to seed the walk (FR-7); it must seed from every queue the
harness actually has.

## Non-functional requirements

- **Router purity preserved (invariant 4).** `route()` still touches no I/O,
  time, or state; it now additionally accepts `Workflow | None`, but the
  decision stays a pure function of `(Task, Workflow | None)`.
- **Decision-making boundaries preserved (invariants 2, 3, 8).** The dispatcher
  keeps sole ownership of "where next," including the new "finish by default"
  case; the consumer keeps not branching on outcome; routing still ignores
  `repository`/`worktree`.
- **`test_architecture.py` keeps passing unmodified** — no new import of
  `drivers/` into `router.py`/`dispatcher.py`/`consumer.py`.
- **Backward compatible on disk.** Existing task JSON files (with a
  `workflowTemplate` string) and existing workflow JSON files must continue to
  load unchanged after this ships — no forced migration.
- **No performance regression.** Discovering queues from `agents/*.json`
  instead of one workflow's `steps()` must not add I/O to the hot dispatch
  loop — it happens once, at `build()` time, exactly like today.

## Data model

- `Task.workflow_template` becomes optional (`str | None`, default `None`,
  serialized key stays `workflowTemplate` for backward compatibility). A
  present value keeps meaning "load this workflow and consult it for
  redirects"; `None` means "no workflow — finish after this step."
- A new field is needed to say *which* step a workflow-less task targets,
  since `status` starts at `None` for every fresh task (this is how the board
  recognizes `todo` — invariant 22 — and must keep being true). Working name:
  `Task.step: str | None` — the entry queue. When `workflow_template` is set
  and `step` is absent, entry resolves to `workflow.start` exactly as today
  (fully backward compatible). When `workflow_template` is absent, `step` is
  required at submission time and is the task's one and only queue.
- `Workflow` itself is unchanged in shape (`name`, `start`, `transitions`) —
  FR-3's new default is a router-level rule, not a schema change.

## Interfaces

- **CLI:** `harness submit` gets a `--step <name>` option, mutually exclusive
  with `--workflow` (documented as: use one or the other). `harness init` no
  longer requires `--workflow`; without it, it just ensures `repos.json`
  exists and lets the operator drop agent files in on their own.
- **`TaskSource` (GitHub):** `GithubTaskSource` gains the equivalent optional
  targeting — a repo/label mapping to a bare step name instead of a workflow —
  so an issue can be ingested as a single-agent task. Exact label scheme is a
  follow-up design decision (see Open Questions); this task only needs the
  underlying `Task`/router support to exist.
- **API/UI:** no new endpoints. `BoardView` output is unchanged in shape; only
  the column set it can now represent grows (a workflow-less task's one-step
  "pipeline").

## Dependencies and scope

**Depends on:** the existing router/dispatcher/consumer split (phase 1), the
agent catalog and `ClaudeCliBehavior` (phase 3) — a workflow-less task must
still resolve an `AgentSpec` for its step exactly like a workflow step does
today.

**In scope:** `models.py` (`Task` fields), `router.py` (optional workflow),
`dispatcher.py` (workflow lookup becomes conditional), `app.py` (`build()`
queue discovery, FR-7), `projection.py` (`column_order` without a mandatory
single `Workflow.start`), `cli.py` (`submit`/`init` flags), `fs_workflows.py`
(no change to the file format itself), test coverage for all of the above.

**Out of scope for this task:**
- Redesigning `GithubTaskSource`'s label scheme for step-only issues (noted as
  a follow-up; this task only unblocks it at the model/router level).
- Letting a task switch workflows mid-flight, or run against more than one
  workflow at once.
- Any change to the wire format of workflow JSON files.
- UI visual redesign beyond column-ordering correctness.

## Rough plan

1. **Model:** make `Task.workflow_template` optional; add `Task.step` (entry
   step for a workflow-less task); update `to_dict`/`from_dict` and every
   in-repo constructor site (CLI `submit`, `MemoryTaskSource`,
   `GithubTaskSource`).
2. **Router:** change `route(task, workflow)` to `route(task, workflow: Workflow
   | None)`. Fresh-task branch picks `task.step` when `workflow is None`,
   else `workflow.start` (unchanged). Add the FR-3/FR-4 "known step, no edge →
   Finished; unknown step → Failed" split for the workflow-present case, and
   the FR-2 "no workflow → always Finished after one hop" case.
3. **Dispatcher:** only call `WorkflowRepository.get(...)` when
   `task.workflow_template` is set; pass `None` through to `route()`
   otherwise. Keep `WorkflowNotFound` handling unchanged for the "named but
   missing" case.
4. **Queue/consumer discovery (`app.py`):** replace "queues = one workflow's
   `steps()`" with "queues = every declared agent" (default behavior driver
   keeps its current fixed step list for the in-memory/dummy path). Wire
   `behavior_for(step)` unchanged — it already keys off the step name, not the
   workflow.
5. **Projection:** rewrite `column_order` to seed its reachability walk from
   every known queue (not one `workflow.start`), still following any
   workflow's transitions where present for ordering, falling back to
   declaration order for workflow-less steps.
6. **CLI:** add `--step` to `submit`; make `init` tolerate no `--workflow`;
   update `_write_default_agents` to also handle a plain list of agents with
   no workflow behind them.
7. **Tests:** update `test_router.py` (new optional-workflow cases plus the
   FR-3/FR-4 split), `test_dispatcher.py`, `test_cli.py`, `test_app.py`,
   `test_projection.py`, and extend `test_phase3_e2e.py`/`test_smoke_git.py`
   with a single-agent, no-workflow run end to end.
8. **Docs:** update `CLAUDE.md`'s invariants list only if the design step
   concludes an invariant's wording needs to change (e.g. invariant 8's
   "solely on (status, lastOutcome)" — confirm it still holds once `workflow`
   presence is also read, and word it precisely if not).

## Open questions

- **Exact queue-discovery mechanism (FR-7).** Recommended default: derive the
  live step queues from `agents/*.json` at `build()` time (every agent is a
  queue by construction, matching "an agent/queue is a complete unit of work
  by default" literally). Alternative considered: union the steps of every
  `workflows/*.json` file present. Left for the design step to pick — the
  agent-driven default is simpler and matches the phase-3 catalog model
  already in place.
- **Naming of the new entry-step field.** Proposed `Task.step`; could also be
  folded into `workflow_template` by allowing that field to name either a
  workflow *or* a bare step (resolved by trying the workflow repository first,
  falling back to treating the name as a step). The two-field design was
  chosen here because it keeps `route()` free of any repository lookup
  (invariant 4) — the caller (dispatcher) still does all I/O and hands the
  router a plain `Workflow | None`.
- **Accepted trade-off in FR-4.** A workflow-less task has no membership set
  to validate `status` against, so a hand-corrupted `status` on such a task
  would silently finish rather than fail. This mirrors the fact that, without
  a workflow, the dispatcher itself never had a second opinion on `status` to
  begin with — it is judged an acceptable, narrow trade-off rather than a
  regression, but the design step should confirm.
- **GitHub source targeting scheme.** How an issue picks "run this one agent"
  vs "run the pipeline" (a second label? a different select-label per queue?)
  is left to a follow-up task; this task only needs to unblock it at the
  model/router level.
- **`--step`/`--workflow` combined use** (e.g., inject a task directly into
  the middle of a pipeline while still letting the workflow redirect it
  onward from there) is plausible future value but not required by this
  task's title; flagged here rather than built, to keep this change reviewable.
