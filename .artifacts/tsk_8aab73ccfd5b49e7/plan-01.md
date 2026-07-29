# Plan: reject a `{"step": X}` Process/Trigger target with no dispatch queue

## Summary

A Process (or bare Trigger) target of the form `{"step": X}` is accepted as
"fireable" whenever `X` names *either* a known step *or* a served workflow,
but the dispatcher can only route into a step-queue keyed set. When `X`
happens to be a served workflow's *name* rather than an actual step, or is a
catalog agent/step the currently-running instance never built a queue for,
the task dies at dispatch with `step 'X' has no queue' → failed/` and loops
into self-healing. This plan tightens target validation to match the real
dispatch surface and makes the residual dispatcher-side dead end (the
live-config/restart-ordering case) actionable instead of a generic failure.

## Context

Task `tsk_936d9920688e414f` (repo `personal_assistant`) failed with
`step 'nanoclaw-sweep' has no queue`, even though `agents/nanoclaw-sweep.json`
and `processes/nanoclaw-sweep.json` both existed and loaded successfully.
Investigation (see the task body) found a concrete, reproducible validation
seam:

- `app.py:699` builds one merged set — `known_targets = set(known_steps) |
  set(resolved)` (`resolved` = served **workflow** names) — and hands it to
  both `FilesystemProcessRepository.build()` (`app.py:709`) and, via
  `cli.py:_scheduled_sources` (`cli.py:809-828`, fed by an equivalent merge
  at `cli.py:1873-1886`), `FilesystemTriggerRepository.build()`.
- `_parse_target` in both `drivers/fs_processes.py:225-246` and
  `drivers/fs_triggers.py:139-158` then checks *either* key (`workflow` or
  `step`) against that same undifferentiated `known_targets` set. A
  `{"step": "resolver"}` target (a served workflow's *name*, not a step)
  therefore validates cleanly — `"resolver" in known_targets` is true because
  it's a workflow name — yet `step_queues` (`app.py:459-464`) is keyed only by
  `known_steps`, so `Dispatcher.tick` (`dispatcher.py:76-78`) has nothing to
  route into and can only call `self._fail(...)`.
- Separately, `FilesystemProcessAdmin.write` (`drivers/fs_processes.py:485-505`)
  always calls `compile_process(..., known_targets=None)` — the dashboard's
  "add/edit Process" form performs **no** target-reachability validation at
  all today (a deliberate, documented gap: the served-workflow set isn't
  available at admin-write time, unlike `known_repositories`). This is the
  most direct path to writing a target that will dead-end at dispatch without
  any restart in between to catch it.
- The `nanoclaw-sweep` case itself reached the failing state via a second,
  narrower path: agent + process files added against an already-running
  service with no live-reload of `agents/`/`processes/` — so the step-queue
  set stayed frozen at the previous `app.build()` call. That path is a
  symptom of the same underlying gap (something the harness treats as
  fireable has no receiving queue) but isn't fully fixed by tightening
  `_parse_target` alone, since it's a staleness problem, not a
  same-instant misvalidation.

This is a harness correctness bug (validation doesn't match the runtime
dispatch surface), not a content problem in the `personal_assistant` repo's
own agent/process files.

## Functional requirements

**FR-1 — `{"step": X}` validates against actual step-queue keys only.**
`_parse_target` (both `drivers/fs_processes.py` and `drivers/fs_triggers.py`)
must check a `step` target against the set of names `step_queues` is actually
keyed by (today: `known_steps` — served workflow steps ∪ catalog agent
names), never against served workflow *names*.
- AC1: a process/trigger file with `{"target": {"step": "<name-of-a-served-
  workflow>"}}` (where that name is not also a step/agent) fails
  `FilesystemProcessRepository.build()` / `FilesystemTriggerRepository.build()`
  with a `ProcessValidationError`/`TriggerValidationError` (`field="target"`),
  instead of compiling successfully.
- AC2: every existing, currently-valid process/trigger file (step target
  really is a step, or workflow target really is a served workflow name)
  continues to compile unchanged — no behavior change for correct configs.

**FR-2 — `{"workflow": X}` validates against served workflow names only.**
Symmetric tightening: a `workflow` target must be checked against served
workflow names only, not the merged set, so a workflow target that actually
names a step/agent is rejected too (same class of bug, opposite direction;
currently latent since no reported incident hit it, but the same seam).
- AC: a file with `{"target": {"workflow": "<name-of-a-step-only-agent>"}}`
  fails compilation with a clear message.

**FR-3 — Thread two distinct sets through both repositories' call sites.**
`compile_process`/`FilesystemProcessRepository.build()` and
`FilesystemTriggerRepository.build()`'s public signatures change from one
`known_targets: set[str] | None` parameter to two —
`known_steps: set[str] | None` and `known_workflows: set[str] | None` (naming
to be finalized in design). Update every call site:
- `app.py` (~line 699-711): stop collapsing `known_steps`/`resolved` into one
  union; pass both through to `FilesystemProcessRepository.build()`.
- `cli.py:_scheduled_sources` (~803-828) and its caller (~1873-1886): same
  split, passed to `FilesystemTriggerRepository.build()`.
- AC: `grep -rn known_targets src/harness` after the change shows no site that
  still merges steps and workflow names into one set before a target check.

**FR-4 — Close the admin write-time validation gap.**
`FilesystemProcessAdmin.write` (`drivers/fs_processes.py:485-505`) currently
passes `known_targets=None`, skipping target validation entirely on every
dashboard "add/edit Process" submission. Wire it the same `known_steps` /
`known_workflows` the running instance actually has (mirroring how
`known_repositories` is already threaded into the admin today), so a
dashboard-authored process that names an unreachable target is rejected at
save time with a field-scoped error, not written to disk to fail (or sit
inert) later.
- AC: submitting a process via `ProcessAdmin.write` with a step target that
  names a served workflow (or an unknown name) raises
  `ProcessAdminValidationError` with `field="target"` and nothing is written.
- Open question (see below): whether the admin has access to a live
  `known_steps`/`known_workflows` snapshot at all, given it's wired
  independently of `app.build()` — needs design-phase confirmation.

**FR-5 — Make the dispatcher's dead end actionable (defence in depth).**
When `Dispatcher.tick` still hits `step 'X' has no queue` (FR-1–FR-4 close
the same-instant validation gap, but not a live-edit/restart-ordering
desync where a step was valid at the process/trigger's own validation time
but the *running* instance's frozen `step_queues` doesn't have it), the
failure reason string should say more than today's bare `"step 'X' has no
queue"` — e.g. suggest restarting the harness service / reconciling the
queue set, since that is the only remedy available to an operator seeing
this in the board's failure reason.
- AC: the `failed` task's history/reason text for this dispatch failure
  includes actionable wording (exact copy decided in design), with no change
  to dispatch *routing* behavor (invariant: the dispatcher still only decides
  where a task goes, per ADR-0002/ADR-0018's finisher/outcome-vocabulary
  precedent of not overloading it with new decisions).
- Constraint: `dispatcher.py` may depend only on ports (module map / "Base"
  and "Ports" layers) — it must not gain a dependency on `AgentCatalog` or
  `RepositoryRegistry` to "know" what's plausible vs. unknown. Default to a
  generic, always-correct message rather than a catalog-aware distinction
  (see Open questions).

**FR-6 — Regression tests.**
- Unit tests in `tests/test_fs_processes.py` and `tests/test_fs_triggers.py`
  covering FR-1/FR-2 (step-named-as-workflow and workflow-named-as-step, both
  rejected; both existing correct shapes still accepted).
- A `tests/test_app.py` (or equivalent end-to-end) case: `app.build()` with a
  `processes/*.json` targeting a served workflow's name via `{"step": ...}`
  fails fast at build, the same way an unknown target already does today —
  never reaches a live `Dispatcher.tick` dead end.
- An admin-level test (`tests/test_process_admin.py` or wherever
  `FilesystemProcessAdmin` is currently tested) for FR-4.
- A dispatcher-level test for FR-5's message content, keeping the existing
  "unknown step → failed/" test's routing assertions intact.

## Non-functional requirements

- **Backward compatibility**: every currently-valid `processes/*.json` /
  `triggers/*.json` file (in this repo's own `processes/`/`triggers/` and in
  any deployed instance) must keep compiling identically. This is a
  tightening of an existing check, not new required config.
- **No new runtime cost**: the fix is a set-membership check at startup
  (`app.build()`) and at one admin write call — no added I/O, no polling
  loop, no live-reload of config directories (explicitly out of scope, see
  below).
- **Message clarity**: FR-5's error text is operator-facing (surfaces on the
  board); it should name the concrete remedy (restart / re-sync) rather than
  restating the mechanical fact already in the existing message.

## Data model

No new persisted entities or schema changes. This only reshapes two
**derived, in-memory sets** that already exist:
- `known_steps` (`app.py:420-426`, `cli.py:~1873-1886`) — served workflow
  steps ∪ catalog agent names. Already exactly what `step_queues` is keyed
  by (`app.py:459-464`).
- `resolved` / `served_names` (`app.py:399-400`, `cli.py`) — served workflow
  *names*. Already exists; just needs to stop being unioned with
  `known_steps` before reaching `_parse_target`.

No change to `Task`, `Process`/`ProcessFields`, `Trigger` file schema, or any
queue/board-visible data.

## Interfaces

- `drivers/fs_processes.py::_parse_target(where, target, known_steps,
  known_workflows)` — signature change (was `known_targets`).
- `drivers/fs_processes.py::compile_process(...)` and
  `FilesystemProcessRepository.build()` / `_build_one()` — same split.
- `drivers/fs_triggers.py::_parse_target(...)` and
  `FilesystemTriggerRepository.build()` — same split, mirroring the sibling
  module (these two are structural twins already, per the module map).
- `drivers/fs_processes.py::FilesystemProcessAdmin.__init__`/`write` — gains
  whatever known-steps/known-workflows snapshot design decides to thread in
  (constructor param(s) vs. a callable/factory, TBD in design phase — the
  admin doesn't own an `app.build()` call, so this needs a supply mechanism
  analogous to how `checks`/`registry` are already injected).
- `dispatcher.py::Dispatcher.tick` — only the `reason` string passed to
  `self._fail(...)` at the `step {decision.step!r} has no queue` site
  changes; no new method, no new port, no new constructor dependency (per
  the constraint in FR-5).
- No API/UI route signatures change; no new endpoints.

## Dependencies and scope

**In scope:**
- `src/harness/drivers/fs_processes.py`
- `src/harness/drivers/fs_triggers.py`
- `src/harness/app.py` (call-site wiring)
- `src/harness/cli.py` (call-site wiring, `_scheduled_sources` and its
  `known_targets` computation)
- `src/harness/dispatcher.py` (FR-5, message text only)
- Associated tests (`tests/test_fs_processes.py`, `tests/test_fs_triggers.py`,
  `tests/test_app.py`, dispatcher tests, admin tests)
- A new ADR (next number, `docs/adr/0022-...md`) documenting the
  validate-against-the-real-dispatch-surface rule, following this repo's ADR
  convention (`test_adr_docs.py` requires `## Context`/`## Decision`/
  `## Consequences` and a `Status:` line).

**Out of scope (explicitly, per the task's own framing):**
- Live-reload of `agents/`/`processes/`/`triggers/` directories while the
  service runs. This would independently close the restart-ordering path
  FR-5 only mitigates, but it's a materially larger feature (a watch loop,
  a reconciliation strategy for in-flight tasks) and isn't what was asked
  for here.
- Any change to the self-healing (`failed-tasks` check) behavior itself —
  it already does exactly what it's supposed to with a task that lands in
  `failed/`; the bug is that this class of task shouldn't be dispatchable
  in the first place.
- The `personal_assistant` repo's own `agents/nanoclaw-sweep.json` /
  `processes/nanoclaw-sweep.json` content — no evidence either file is
  itself malformed; this is a harness-side gap.
- Any change to `ProcessAdmin`'s `check_names()`/`check_specs()`/
  `sink_kinds()` dropdown-population shape beyond what FR-4 strictly needs.

## Rough plan

1. **Design**: finalize the `_parse_target` signature split (naming,
   whether `known_workflows=None` continues to mean "skip validation" the
   same way `known_targets=None` does today), and decide the FR-4 admin
   wiring mechanism (constructor param vs. injected callable) and the FR-5
   message copy + how it's tested without violating the dispatcher's
   ports-only constraint.
2. **Implement FR-1/FR-2/FR-3**: split `_parse_target` and its callers in
   `fs_processes.py`; mirror the same change in `fs_triggers.py`; update
   `app.py` and `cli.py` wiring to stop merging the two sets.
3. **Implement FR-4**: thread known-steps/known-workflows into
   `FilesystemProcessAdmin`, update its construction site(s) in `cli.py`.
4. **Implement FR-5**: update the dispatcher's failure-reason string at
   `dispatcher.py:76-78`.
5. **Tests**: add the regression tests in FR-6 across both drivers, `app.py`
   build-time behavior, the admin, and the dispatcher message — run the full
   suite (`.venv/bin/pytest -q`).
6. **Docs**: add the new ADR; update the `CLAUDE.md` module map / invariant
   text only if the design phase introduces a genuinely new invariant beyond
   "validate against the real dispatch surface" (likely not — this reads as
   a bug fix, not a new architectural rule, but confirm during design).
7. **Land**: per this repo's git conventions, commit straight to `main` with
   conventional-commit messages (`fix: ...`), no branch/PR for the harness's
   own repo.

## Open questions

1. **Does `FilesystemProcessAdmin` have access to a live `known_steps`/
   `known_workflows` snapshot at all?** It's constructed in `cli.py`
   independently of `app.build()`'s run (per its own docstring, it lacks
   the served-workflow set "unlike registry"). Design must confirm whether
   `cli.py` already computes an equivalent set at admin-wiring time (it
   does, at `cli.py:1873-1886`, for `_scheduled_sources`) that can simply be
   passed to the admin's constructor too, or whether that set needs
   recomputation/refactoring into a shared helper. Default assumption: reuse
   the existing `cli.py` computation, don't invent a second one.
2. **Exact wording for FR-5's operator-facing message.** Default: something
   like `"step 'X' has no queue (if this step or process/trigger was added
   or edited recently, restart the harness service to rebuild its queue
   set)"`. Finalize in design so it reads well on the board's failure
   reason column.
3. **Should `known_workflows=None` (validation skipped) remain a supported
   escape hatch**, matching today's `known_targets=None` behavior (used
   where no served-workflow context exists, e.g. some test constructions)?
   Default: yes, preserve the same "None disables this check" convention for
   both new parameters independently, so `known_steps=None` and
   `known_workflows=None` each independently skip only their own half of the
   validation.
4. **Is a new ADR warranted, or is this simply a bug fix?** Default: add a
   short ADR — the underlying rule ("validate a target against the set it
   will actually be dispatched into, never a broader superset") is a
   reusable principle for any future target-shaped validation, worth
   recording per this repo's habit of an ADR per non-obvious rule. If design
   disagrees, downgrade to a `fix:` commit with no ADR and drop step 6's ADR
   task.
