# Plan: serve multiple workflows in a single running harness

## Summary

A task already carries its own `workflow_template` and the dispatcher already
resolves the right `Workflow` per task (`dispatcher.py:60`,
`self._workflows.get(task.workflow_template)`), but `app.build()` still wires
the rest of the harness — step queues, the board projection, the consumer
loops — around exactly **one** `Workflow` object (`app.py:233-259`). A task
naming a second, valid workflow definition routes to a step that has no
queue and fails with a confusing `step '<step>' has no queue`
(`dispatcher.py:72-74`), even though its definition file loads fine. This
plan closes that gap: one running harness serves a set of workflows, queues/
consumers/projection are built for the union of all their steps, and a task
for a workflow the harness doesn't serve fails early with a clear message.

## Context

Investigation (see the originating issue, `onpaj/harness_v2#29`) already
mapped exactly what's in place and what's missing:

**Already correct, no change needed:**
- `Task.workflow_template` and per-task routing via
  `WorkflowRepository.get(task.workflow_template)` in `Dispatcher.tick()`.
- `FilesystemWorkflowRepository` loads any `<root>/<name>.json`; several
  definitions can already coexist on disk.
- `router.route()` stays a pure function of `(status, lastOutcome)` plus the
  task's own workflow — untouched by this change (invariant #4).

**The gap — the runtime is wired for one workflow:**
- `app.build(root, workflow_name, ...)` takes a single name, resolves one
  `Workflow`, and derives `step_queues` (`app.py:254-259`),
  `BoardProjection` (`app.py:236`), and the consumer list (`app.py:309-321`)
  from it alone.
- `Harness.workflow` is a single `Workflow`; `cli.py` (`_init`, lines
  103/107) and `test_app.py:67` read it directly.
- `harness run --workflow <name>` accepts exactly one name
  (`cli.py:757`), so there is no way today to tell a running harness "serve
  A and B".

This is scoped narrowly to the runtime/wiring layer. It does **not** touch
the router, the dispatcher's routing logic, or the consumer's behavior
contract — only what `app.build()` constructs and how `cli.py` tells it what
to construct.

## Functional requirements

**FR-1 — `harness run` can serve more than one workflow.**
- `--workflow` becomes repeatable: `harness run --workflow default --workflow hotfix`
  serves exactly those two.
- A new `--all-workflows` flag serves every valid definition found under
  `<root>/workflows/*.json`, discovered via a new `WorkflowRepository.names()`
  method (mirrors `RepositoryRegistry.names()` in `ports/repos.py`).
- *Acceptance:* running with two `--workflow` flags against two definitions,
  each carrying a distinct step name, produces a harness whose step queues
  cover both; running with `--all-workflows` against a workflows directory
  with three definition files serves all three without naming them.

**FR-2 — Existing single-workflow behavior is unchanged by default.**
- With no `--workflow` flag at all, `harness run` serves exactly
  `DEFAULT_WORKFLOW` ("default"), byte-for-byte the same as before this
  change — no other file lying in the workflows directory is picked up
  unless explicitly asked for (`--workflow` or `--all-workflows`).
- `harness init` and `harness submit` are untouched: they already operate on
  one workflow name per invocation and stay that way.
- *Acceptance:* a suite run with only `--workflow default` (or nothing)
  passes identically to before; `harness init`/`submit` CLI surface is
  unchanged.

**FR-3 — Step queues are the union of every served workflow's steps.**
- `app.build()` accepts one or more workflow names, resolves each via
  `WorkflowRepository.get()` (fail-fast, same as today for a single name),
  and builds exactly one `FilesystemTaskQueue` per **distinct step name**
  across all of them.
- Two served workflows that both declare a step called `review` share the
  *same* queue, the *same* consumer loop, and the *same* behavior/agent
  persona for that step — this is a deliberate simplification, not an
  oversight (see Open questions). A workflow that needs different behavior
  for what is conceptually a different step must give that step a different
  name.
- *Acceptance:* two workflows, one with `plan → build → end`, one with
  `plan → review → end`, served together, produce three consumer loops
  (`plan`, `build`, `review`), with `plan` shared.

**FR-4 — The board projection covers every served workflow.**
- `BoardProjection` accepts a sequence of workflows instead of one. Its
  column order is the union of each workflow's reachability-ordered steps
  (first workflow's reachable steps, in served order, then the next
  workflow's not-yet-seen steps, ...), still bracketed by `todo`/`done`/
  `failed`.
- *Acceptance:* the board for two served workflows shows one column per
  distinct step name (no duplicate columns for a shared step name), and a
  task shows up in the right column regardless of which served workflow it
  belongs to.

**FR-5 — A task for an unserved workflow fails clearly.**
- Today, `FilesystemWorkflowRepository.get()` will happily load *any*
  definition file that exists on disk, even one the running harness wasn't
  told to serve — so a task naming it would sail past workflow resolution
  and only fail later at the queue lookup with the generic "no queue"
  message. A thin decorator around the repository, scoped to exactly the
  served names, closes this: it raises `WorkflowNotFound` with a message
  naming the unserved workflow and listing what *is* served, before the
  dispatcher ever reaches the queue lookup.
- *Acceptance:* a harness serving only `default`, given a task whose
  `workflow_template` is `other` (a valid, loadable definition on disk, but
  not passed to `--workflow`), fails that task into `failed/` with a reason
  mentioning `other` is not served — not "step '<step>' has no queue".

**FR-6 — Tests.**
- Two workflows served at once, tasks routed correctly to each (including a
  shared step name).
- A task for an unserved (but existing) workflow fails cleanly per FR-5.
- A task for a genuinely nonexistent workflow still fails as it does today
  (`WorkflowNotFound` from a missing file).
- Single-workflow runs (default, or explicit `--workflow`) behave exactly as
  before — a regression check, not just a new-feature check.
- `BoardProjection` with multiple workflows: column union, no duplicates.
- `WorkflowRepository.names()` (discovery) on the filesystem driver.

## Non-functional requirements

- **No behavior change for the common case.** A single-workflow deployment
  (still the expected default for most operators) must not change its board
  layout, event shapes, or queue file layout.
- **Fail fast, not silent.** An unserved or missing workflow is a startup- or
  dispatch-time error with a specific message, never a silently dropped task.
- **No new I/O per tick.** Discovery (`names()`) runs once at `build()` time,
  not on the dispatcher/consumer hot path — `route()` stays a pure function
  and the per-tick cost is unchanged.

## Data model

No changes to `Task`, `Workflow`, `Transition`, or any on-disk task/queue
format. The only new shape is at the wiring boundary:

- `WorkflowRepository.names() -> tuple[str, ...]` — a new port method,
  implemented by `FilesystemWorkflowRepository` (glob `<root>/workflows/*.json`,
  return the stems; unreadable files are skipped here and still fail loud
  later from `.get()` if actually requested — mirrors
  `RepositoryRegistry.names()`'s "quiet on discovery, loud on resolve" split).
- A new small decorator, e.g. `ServedWorkflowRepository(inner, names)`,
  wrapping any `WorkflowRepository` and rejecting names outside the served
  set with a `WorkflowNotFound` that names what's missing and what's served.
  Lives alongside `FilesystemWorkflowRepository` in `drivers/fs_workflows.py`
  (or a small sibling module) — it's still just a driver-level detail, wired
  in `app.py`.
- `Harness.workflow: Workflow` becomes `Harness.workflows: dict[str, Workflow]`
  (name → resolved workflow, the served set). The two current readers
  (`cli.py` `_init`, `test_app.py`) index into it by name instead of reading
  a single attribute.
- `app.build()`'s second positional parameter (`workflow_name: str`) widens
  to `workflows: str | Sequence[str]` — a single string keeps meaning exactly
  what it means today (one served workflow), so the ~19 existing test call
  sites and every current caller of `build(root, "default", ...)` keep
  working unchanged; a list/tuple is the new multi-workflow entry point.

## Interfaces

- **CLI (`harness run`):**
  - `--workflow NAME` (repeatable; unset ⇒ defaults to just `["default"]`,
    identical to today).
  - `--all-workflows` (flag; mutually exclusive with `--workflow` — reject
    the combination with a clear CLI error rather than silently picking one).
- **CLI (`harness init`, `harness submit`):** unchanged — still one
  `--workflow` per invocation.
- **Events:** the `started` event's payload changes from a single
  `workflow=<name>` to a plural `workflows=[<name>, ...]` (sorted). The one
  test asserting the old shape (`test_projection_events.py:50`) is a
  hand-built fixture event, not a behavior assertion — updated alongside.
- **Board/API:** no change to `BoardView`/`ArtifactView`/routes — only what
  feeds `BoardProjection` at construction changes.

## Dependencies and scope

- Depends on nothing unmerged for its own correctness, but two things are
  worth flagging:
  - A separate, not-yet-merged line of work (branch
    `harness/tsk_2fb79172220a45f3`, commit `23bfd37`) adds per-step
    `maxParallel` limits (`Workflow.max_parallel_for`) and spins up that many
    consumer loops per step in `Harness.run()`. It does **not** exist on
    `main`/this branch today. If it lands first, this task's step-queue
    union (FR-3) needs one extra rule for a step name shared by workflows
    with *different* `maxParallel` values for that step — flagged in Open
    questions rather than solved here, since it may not land before this
    does.
  - The issue text mentions `FilesystemWorkflowRepository` already validating
    `maxParallel` — that validation also lives only on the unmerged branch
    above, not on this one. This plan does not depend on it and doesn't
    introduce it.
- **Out of scope** (per the issue): a UI for switching between workflows;
  dynamically reloading workflow definitions while the harness is running
  (a served set is fixed for the process's lifetime, exactly as a single
  workflow is today); per-workflow namespacing of shared step names (see
  FR-3's deliberate simplification); validating a task's workflow at
  `harness submit` time (submit doesn't open/validate the workflow file
  today either — no regression, but no new up-front check there).

## Rough plan

1. **Port + driver:** add `WorkflowRepository.names()` to
   `ports/workflows.py`; implement it on `FilesystemWorkflowRepository`
   (`drivers/fs_workflows.py`). Add `ServedWorkflowRepository` (or similarly
   named decorator) next to it.
2. **Projection:** change `column_order`/`BoardProjection` in `projection.py`
   to take a sequence of workflows and merge column order across them
   (union, first-seen order per FR-4). Update the ~19 existing
   `BoardProjection(WORKFLOW)` call sites in tests to
   `BoardProjection([WORKFLOW])`.
3. **Wiring:** in `app.py`, widen `build()`'s workflow parameter to
   `str | Sequence[str]`, normalize to a tuple of names up front, resolve
   each via the (unwrapped) `FilesystemWorkflowRepository`, build the union
   `step_queues` (FR-3), wrap the repository handed to `Dispatcher` in
   `ServedWorkflowRepository` (FR-5), replace `Harness.workflow` with
   `Harness.workflows: dict[str, Workflow]`, and adjust the `started` event.
4. **CLI:** in `cli.py`, make `--workflow` repeatable on the `run`
   subcommand, add `--all-workflows`, resolve the served name list before
   calling `build()`, and fix the two `harness.workflow` reads in `_init`.
5. **Tests:** update the mechanical `BoardProjection`/`harness.workflow`
   call sites (regression safety net), then add the new coverage from FR-6 —
   two workflows served together (including a shared step name), an
   unserved-workflow failure, a nonexistent-workflow failure, and
   `names()`/`ServedWorkflowRepository` unit tests.
6. Run `.venv/bin/pytest -q` and confirm the full suite (existing +
   new) passes.

## Open questions

- **Shared step name across workflows:** resolved here as "one queue, one
  consumer, one behavior per step name, workflow-agnostic" (FR-3), because
  that's what the codebase already does for a single workflow (`catalog.get(step)`
  and `behavior_for(step)` are already keyed by step name alone, not by
  workflow). Flagging in case design/architecture wants a namespaced
  alternative — that would be a materially bigger change (agent catalog and
  behavior lookup would both need a `(workflow, step)` key).
- **Interaction with per-step `maxParallel`** (unmerged branch
  `tsk_2fb79172220a45f3`): if that work lands before or alongside this one,
  a step shared by two served workflows with different `maxParallel` values
  needs a resolution rule (e.g., take the max, or require they match and
  fail fast on conflict). Left unresolved pending that branch's fate.
- **`--workflow` and `--all-workflows` together:** proposed as mutually
  exclusive with a CLI error; could instead mean "everything found, plus
  explicitly confirm this list" — picked the stricter, less surprising
  option as the default.
- **`--github-workflow` validation:** GitHub-sourced tasks are stamped with
  a single `--github-workflow` name (`cli.py:775`); nothing today checks
  that name is among the served set at startup. Worth a fail-fast check
  (the source would otherwise create tasks that fail at dispatch minutes
  later) — proposed as a small addition, not blocking this task's core
  scope.
