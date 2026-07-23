# Plan: Make workflows optional (revision 2 — finalized after design + architecture review)

This revises `plan-01.md` to close every open question it left, using the
decisions made in `design-01.md` and the gaps `architecture-01.md` found
against the actual source. Nothing in the original motivation changes; this
revision exists so the FRs, scope and rough plan are the literal contract the
dev step implements against, with no open question left implicit.

## Summary

Today every task is bound to exactly one `Workflow` (a JSON file of explicit
`from → on → to` edges), and the harness process itself is wired to exactly
one workflow at `build()` time. This task inverts the default: **a single
agent/queue is a complete unit of work on its own** — submit a task straight
at a step, the step's agent runs once, and the task finishes. A **workflow
becomes optional and purely additive**: when a task carries one, its edges
*redirect* the task onward past a step instead of letting it finish there;
where the workflow is silent, the default (finish) still applies. Multi-step
pipelines (`plan → design → … → land`) keep working exactly as they do today
— this is a generalization, not a replacement.

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
`harness submit` accepts `--step <name>` as a mutually exclusive alternative
to `--workflow <name>`. The resulting task carries `workflow_template=None`,
`step=<name>`. The same applies at the `TaskSource` layer — `GithubTaskSource`
gains an optional `step` constructor parameter alongside its existing
`workflow` parameter, mutually exclusive the same way at the CLI boundary
(`--github-step` alongside `--github-workflow` on `harness run`).
*Acceptance:* `harness submit --step development ...` writes a task with
`workflowTemplate: null`, `step: "development"` and reaches the
`development` queue. `harness submit --workflow X --step Y` is rejected by
argparse at parse time (mutually exclusive group), never reaches `_submit`.

**FR-2 — A workflow-less task is routed straight to `end` after one pass.**
A fresh task with no workflow enters the queue named by `task.step`; once
that step's agent returns any outcome, the dispatcher finishes the task
(`end`, i.e. the `done` queue) without requiring any further configuration.
*Acceptance:* `route(task, workflow=None)` with `status=None` yields
`MoveTo(task.step)`; with `status=<step>` and any outcome, yields
`Finished()`. An end-to-end run (single agent, `DONE`) lands the task in
`done/` with exactly one history hop out and one back.

**FR-3 — A workflow only adds redirections; it is never required to be
exhaustive.** For a task that does carry a workflow, an edge that exists for
`(status, outcome)` behaves exactly as today (`MoveTo`/`Finished` at an
explicit `end` edge). For a `(status, outcome)` pair the workflow does **not**
cover, the task finishes by default — the same default as FR-2 — rather than
failing, *provided `status` is a step the workflow actually knows about*
(FR-4 draws that line). A workflow's job shrinks to "list the redirects";
anywhere it's silent, the step it's silent about is a complete unit of work.
*Acceptance:* a workflow that defines only `plan --done--> design` (no edge
for `design`'s `done` outcome) sends a task that finishes `design` straight
to `end`, not to `failed/`.

**FR-4 — Corruption/misconfiguration still fails loudly, for tasks that do
carry a workflow; a workflow-less task fails loudly on a reserved step name.**
If a task's `status` is not one of the workflow's known steps at all (typo,
stale definition, hand-edited file), routing still fails into `failed/` with
a clear reason — FR-3's new default only fires for a *known* step with no
outgoing edge, not for an unrecognized one. Separately, `Task.step` (the
workflow-less entry point) is rejected at two points if it names a reserved
terminal (`end`, `failed`) or fails the existing `invalid_workflow_name`
shape check (empty, path-traversal characters): at the CLI/`TaskSource`
boundary (`--step end` is refused before a task is ever written), and
defensively inside `router.route()`'s new workflow-less branch (belt and
suspenders — the router is already the one place that defends against
malformed input for the workflow-present path, via the FR-4 unknown-status
check above).
*Acceptance:* existing `test_unknown_status_fails`-style behavior is
preserved when a workflow is present; `test_missing_edge_fails`-style
behavior changes to `Finished` per FR-3; `harness submit --step end` exits
non-zero with a clear error and writes no task file; `route(Task(status=None,
step="end", workflow_template=None), None)` yields `Failed(...)`, not
`MoveTo("end")`.

**FR-5 — Multi-step pipelines are unaffected.** The built-in `default`
workflow (`plan → design → architecture → development → review → land`) and
any other hand-authored multi-edge workflow keep behaving exactly as they do
today, edge for edge. This is verified by the full existing e2e/smoke suite
without modification beyond the router/dispatcher tests called out in FR-3/4.

**FR-6 — `harness init` does not require a workflow to produce a usable
harness.** An operator can initialize a harness with one or more standalone
agents and no workflow file at all, and still `submit`/`run` against them,
via a new `--no-workflow` flag. Plain `harness init` (no flags) stays
byte-for-byte identical to today's behavior — this is additive, not a
default change.
*Acceptance:* `harness init --root X --no-workflow` succeeds and writes no
`workflows/default.json`; `harness submit --root X --step <agent>` on an
agent created afterwards reaches that queue; `harness init --root X` (no
flags) produces the same files it does today.

**FR-7 — The set of live queues is no longer derived from a single
workflow's `steps()`.** Since a harness process must now be able to run
workflow-less tasks alongside pipeline tasks — potentially against several
different workflow files at once — the set of step queues is the **union**
of every step named across every workflow file under `workflows/`
(`WorkflowRepository.names()` → `.get(name).steps()`, best-effort — an
unparseable file is skipped during discovery, not fatal) and every agent
declared under `agents/` (`AgentCatalog.names()`), when a catalog is wired
in. With exactly one workflow file and no catalog (today's shape), the union
degenerates to exactly what `build()` computes today — this is a strict
backward-compatible generalization, not a behavior change for existing
callers.
*Acceptance:* a `build()` call with two workflow files (`default.json`,
`hotfix.json`) and a catalog with one extra standalone agent (`triage`)
produces step queues for the union of all three sources' steps, with no
duplicates. A `build()` call exactly as today (one workflow, no catalog)
produces exactly the same queue set as before this change.

**FR-8 — The board still renders correctly.** `todo` → single column →
`done` for a workflow-less task; the existing column-ordering-by-reachability
keeps working for pipeline tasks. `column_order`/`BoardProjection` are seeded
from the full known-steps set (FR-7) plus every loadable workflow (for
ordering), not from one mandatory `Workflow.start`. A workflow-less task's
step that no workflow's walk reaches falls back to declaration order, still
landing between `todo` and `done`.
*Acceptance:* a workflow-less task submitted with `--step development`
renders as `todo → development → done` on the board; a pipeline task's
column order is byte-for-byte unchanged from today.

**FR-9 — The task detail view never renders a literal `None`/`null` for the
new nullable fields.** `api/templates/_task.html`'s `workflow` row uses the
same `{{ x or "—" }}` fallback every other nullable field on that page
already uses (`repository`, `worktree`, `status`, `lastOutcome`, `lockId`),
and gains a new `step` row with the same fallback.
*Acceptance:* the detail page for a workflow-less task (`workflow_template
= None`, `step = "development"`) renders `—` in the `workflow` cell and
`development` in the `step` cell; the detail page for a pipeline task
renders unchanged from today.

## Non-functional requirements

- **Router purity preserved (invariant 4).** `route()` still touches no I/O,
  time, or state; it now additionally accepts `Workflow | None`, but the
  decision stays a pure function of `(Task, Workflow | None)`.
- **Decision-making boundaries preserved (invariants 2, 3, 8).** The
  dispatcher keeps sole ownership of "where next," including the new "finish
  by default" case; the consumer keeps not branching on outcome; routing
  still ignores `repository`/`worktree`. Invariant 8's wording is updated
  (see Dependencies and scope) to state precisely that `route()` now also
  reads whether a workflow is present at all, never `step`/`data` for
  routing decisions beyond picking the workflow-less entry queue.
- **`test_architecture.py` keeps passing unmodified** — no new import of
  `drivers/` into `router.py`/`dispatcher.py`/`consumer.py`.
- **Backward compatible on disk.** Existing task JSON files (with a
  `workflowTemplate` string, no `step` key) and existing workflow JSON files
  must continue to load unchanged after this ships — no forced migration.
  `raw.get("workflowTemplate")`/`raw.get("step")` replace the current
  required-key read.
- **No performance regression.** Discovering queues from the union of
  `WorkflowRepository.names()` and `AgentCatalog.names()` instead of one
  workflow's `steps()` must not add I/O to the hot dispatch loop — it happens
  once, at `build()` time, exactly like today.
- **No abstract-class instantiation breakage.** Both new `.names()` methods
  land as concrete implementations on every existing subclass
  (`FilesystemWorkflowRepository`, `MemoryWorkflowRepository`,
  `FilesystemAgentCatalog`, `MemoryAgentCatalog`) in the *same* change that
  adds the abstract method to the port — never as a follow-up. Landing the
  abstract method first would make every existing `MemoryAgentCatalog(...)`
  call site raise `TypeError` at collection time across the test suite.

## Data model

- `Task.workflow_template` becomes optional (`str | None`, default `None`,
  serialized key stays `workflowTemplate` for backward compatibility). A
  present value keeps meaning "load this workflow and consult it for
  redirects"; `None` means "no workflow — finish after this step."
- `Task.step: str | None = None` — the entry queue for a workflow-less task.
  `status` still starts at `None` for every fresh task regardless (this is
  how the board recognizes `todo` — invariant 22 — and is unaffected). When
  `workflow_template` is set, `step` is unused for routing (entry resolves
  to `workflow.start`, exactly as today) but is still carried on the model
  for symmetry and is expected to be `None` at submission for a
  workflow-carrying task. When `workflow_template` is absent, `step` is
  required at submission time (enforced by the CLI/`TaskSource` boundary,
  not by `Task` itself — the model stays a plain data holder) and is the
  task's one and only queue. `step` is read-only after creation, exactly
  like `repository`/`worktree` — never reassigned once a task exists.
  `id`/`created` move ahead of `workflow_template` in field order so the
  dataclass stays valid once `workflow_template` gets a default (every
  construction site in the repo already uses keyword arguments, confirmed by
  grep — this reorder breaks no caller).
- `Workflow` itself is unchanged in shape (`name`, `start`, `transitions`) —
  FR-3's new default is a router-level rule, not a schema change.
- `WorkflowRepository` and `AgentCatalog` (both `ports/`) each gain an
  abstract `names() -> tuple[str, ...]` / `list[str]`, best-effort
  (unparseable/invalid entries simply absent from the result, never raise).
  `RepositoryRegistry` already has this exact shape (`drivers/fs_repos.py`)
  — this extends the same pattern to the other two repository ports rather
  than inventing a new one.

## Interfaces

- **CLI:** `harness submit` gets `--step <name>`, mutually exclusive with
  `--workflow` via `argparse.add_mutually_exclusive_group()` (a new pattern
  in `cli.py` — no existing subcommand uses it yet). `harness init` gets
  `--no-workflow`; without it, `init` is unchanged. `harness run` gets
  `--github-step`, mutually exclusive with `--github-workflow`; `run`'s
  `--workflow` default changes from the fixed `DEFAULT_WORKFLOW` name to
  `None` (informational only now — see design-01.md's `run` section — since
  queue discovery no longer depends on it).
- **`TaskSource` (GitHub):** `GithubTaskSource.__init__` gains
  `step: str | None = None`. When both `workflow` and `step` are set on the
  driver (should not happen if the CLI boundary is honored, but the driver
  does not itself validate — see FR-4/design's accepted trade-off), `step`
  wins. Exact per-issue label scheme for step-only issues is a follow-up
  (Open Questions).
- **API/UI:** no new endpoints. `BoardView` output is unchanged in shape;
  only the column set it can represent grows (a workflow-less task's
  one-step "pipeline"), and the task detail template gains the `—` fallback
  and new `step` row (FR-9).

## Dependencies and scope

**Depends on:** the existing router/dispatcher/consumer split (phase 1), the
agent catalog and `ClaudeCliBehavior` (phase 3) — a workflow-less task must
still resolve an `AgentSpec` for its step exactly like a workflow step does
today.

**In scope:**
- `models.py` (`Task` fields, field reorder, `to_dict`/`from_dict`)
- `router.py` (optional workflow, the full 8-row truth table from
  design-01.md, reserved-step-name guard)
- `dispatcher.py` (workflow lookup becomes conditional)
- `ports/workflows.py`, `ports/agent.py` (new `.names()` abstract methods)
- `drivers/fs_workflows.py`, `drivers/fs_agents.py`, `drivers/memory.py`
  (`.names()` implementations on all four concrete classes — see the
  NFR above on sequencing)
- `app.py` (`build()` queue discovery via the union rule, FR-7;
  `Harness.workflow` becomes `Workflow | None`)
- `projection.py` (`column_order`/`BoardProjection` seeded from the full
  step set + every loadable workflow, not one mandatory `Workflow.start`)
- `cli.py` (`submit`/`init`/`run` flags, `invalid_step_name` guard)
- `api/templates/_task.html` (FR-9 fallback + new `step` row)
- `drivers/github_source.py` (optional `step` param)
- `fs_workflows.py`'s on-disk format itself is unchanged
- test coverage for all of the above (`test_models.py`, `test_router.py`,
  `test_dispatcher.py`, `test_app.py`, `test_projection.py`, `test_cli.py`,
  `test_fs_workflows.py`, `test_fs_agents.py`, `test_memory.py`,
  `test_github_source.py`, plus one workflow-less end-to-end case added to
  `test_phase3_e2e.py`/`test_smoke_git.py`)
- `CLAUDE.md` invariant 8's wording (see NFR above) — reworded in the same
  PR, not deferred

**Out of scope for this task:**
- Redesigning `GithubTaskSource`'s label scheme for step-only issues (noted
  as a follow-up; this task only unblocks it at the model/router/constructor
  level).
- Letting a task switch workflows mid-flight, or run against more than one
  workflow at once.
- Any change to the wire format of workflow JSON files.
- UI visual redesign beyond column-ordering correctness and the FR-9 `None`
  fix.
- `--agent dummy` + a workflow-less `--step` task getting its own queue when
  the step name doesn't already appear in some workflow file's `steps()`.
  This is a pre-existing asymmetry of the dummy path (it has no notion of
  "declared steps" independent of a workflow file) and stays unaddressed —
  do not build the `extra_steps` escape hatch discussed in design-01.md
  unless a test actually needs it later.
- Allowing `--workflow` and `--step` together (inject mid-pipeline while
  still letting the workflow redirect onward). Plausible future value, not
  required by this task's title; the mutually-exclusive CLI group makes this
  cleanly unbuilt rather than half-built.

## Rough plan

Sequencing follows architecture-01.md's stated prerequisite: the `.names()`
abstract method and every concrete implementation land together, first,
because everything downstream (`app.py`'s union rule, and any test that
constructs a `MemoryAgentCatalog`) depends on it existing.

1. **Repository ports + drivers:** add `names()` to `WorkflowRepository`
   and `AgentCatalog` (`ports/`), and implement it in the same commit on
   `FilesystemWorkflowRepository`, `MemoryWorkflowRepository`,
   `FilesystemAgentCatalog`, `MemoryAgentCatalog` (`drivers/fs_workflows.py`,
   `drivers/fs_agents.py`, `drivers/memory.py`). Add
   `invalid_step_name(name)` next to `invalid_workflow_name`/
   `_invalid_agent_name` (reserved `end`/`failed` + existing shape checks).
2. **Model:** make `Task.workflow_template` optional; add `Task.step`;
   reorder fields; update `to_dict`/`from_dict` and every in-repo
   constructor site (CLI `submit`, `MemoryTaskSource`, `GithubTaskSource`).
3. **Router:** change `route(task, workflow)` to
   `route(task, workflow: Workflow | None)`; implement the full truth table
   (FR-2/3/4), including the workflow-less reserved-step-name defensive
   check.
4. **Dispatcher:** only call `WorkflowRepository.get(...)` when
   `task.workflow_template` is set; pass `None` through to `route()`
   otherwise. Keep `WorkflowNotFound` handling unchanged for the "named but
   missing" case.
5. **Queue discovery (`app.py`):** replace "queues = one workflow's
   `steps()`" with the FR-7 union rule; `Harness.workflow` becomes
   `Workflow | None`; the `"started"` event's `workflow=` field follows
   suit.
6. **Projection:** rewrite `column_order`/`BoardProjection.__init__` to take
   `(steps, workflows=())` and seed the reachability walk from every known
   step across every workflow, falling back to declaration order for
   workflow-less/unreferenced steps.
7. **CLI:** add `--step` to `submit` (mutually exclusive with `--workflow`),
   `--no-workflow` to `init`, `--github-step` to `run` (mutually exclusive
   with `--github-workflow`); apply `invalid_step_name` at the `submit`
   boundary.
8. **GitHub source:** `GithubTaskSource` gains the optional `step`
   constructor param; `poll()` writes it onto the constructed `Task`.
9. **UI:** fix `api/templates/_task.html`'s `workflow` row fallback, add the
   `step` row (FR-9).
10. **Tests:** update `test_models.py`, `test_router.py` (new
    optional-workflow cases plus the FR-3/FR-4 split and the reserved-name
    guard), `test_dispatcher.py`, `test_app.py`, `test_projection.py`,
    `test_cli.py`, `test_fs_workflows.py`, `test_fs_agents.py`,
    `test_memory.py`, `test_github_source.py`; extend
    `test_phase3_e2e.py`/`test_smoke_git.py` with a single-agent,
    no-workflow run end to end.
11. **Docs:** reword `CLAUDE.md` invariant 8 per architecture-01.md's exact
    proposed text ("solely on `(status, lastOutcome)`, and — since this
    task — whether a workflow is present at all; never on
    `repository`/`worktree`, `step`, or `data`").

## Open questions

Resolved by design/architecture review (kept here for the record, not
re-opened):

- **Queue-discovery mechanism (FR-7).** Resolved: union of
  `WorkflowRepository.names()`-derived steps and `AgentCatalog.names()`, not
  agent-catalog-only as originally floated in plan-01 (agent-only would
  silently produce zero queues for every existing in-memory test caller and
  the `--agent dummy` path).
- **Naming of the entry-step field.** Resolved: `Task.step`, a second field
  rather than overloading `workflow_template` — keeps `route()` free of any
  repository lookup (invariant 4).
- **FR-4's workflow-less trade-off.** Resolved as accepted: a workflow-less
  task has no membership set to validate `status` against, so a
  hand-corrupted `status` on such a task would silently finish rather than
  fail. Judged acceptable and explicitly not to be "fixed" by inventing a
  synthetic membership set. The reserved-name guard (FR-4, new since
  plan-01) covers the one part of this surface that *is* worth defending
  (the terminal-name collision), without reintroducing state the router
  doesn't otherwise have.

Still open, deliberately deferred past this task:

- **GitHub source targeting scheme.** How an issue picks "run this one
  agent" vs "run the pipeline" (a second label? a distinct select-label per
  queue?) is left to a follow-up task; this task only needs
  `GithubTaskSource`'s constructor-level optionality to exist.
- **`--agent dummy` + workflow-less `--step` with no matching workflow
  file.** Confirmed out of scope (see Dependencies and scope) rather than
  silently unhandled — flagged explicitly so a future task doesn't
  rediscover it as a surprise.
