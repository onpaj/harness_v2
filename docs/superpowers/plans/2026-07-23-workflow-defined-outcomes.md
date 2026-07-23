# Workflow-defined outcomes — implementation plan (2026-07-23)

Implements `docs/superpowers/specs/2026-07-23-workflow-defined-outcomes-design.md`.
Four packages (A–D) plus a docs pass. A is the type-level foundation the rest
build on, so it lands first; B/C are file-disjoint and can run in parallel after
A; D is the wiring that depends on B. Each package is its own conventional
commit on `claude/workflow-defined-outcomes-wvilv6`.

## Package A — `Outcome`: closed enum → open string (foundation)

**Gap.** `Outcome` is a two-value enum (`models.py:30`); the consumer enforces
it with `isinstance(result.outcome, Outcome)` (`consumer.py:82`). This is the
sole reason a step can report only `done` / `request_changes`.

**Design.**

- `models.py`: delete the `Outcome` enum; add `DONE = "done"` and
  `REQUEST_CHANGES = "request_changes"` as module-level string constants.
  `BehaviorResult.outcome: str`, `AgentRun.outcome: str` (the latter in
  `ports/agent.py`).
- `consumer.py`: the invalid-result guard becomes
  `not isinstance(result, BehaviorResult) or not isinstance(result.outcome, str)
  or not result.outcome`. Still a shape check — no branch on the value
  (invariant #2). `_deliver` already uses `result.outcome` as a string
  (`.value` accesses are dropped).
- Sweep every `Outcome.DONE` / `Outcome.REQUEST_CHANGES` / `outcome.value`
  usage to the constants / plain strings: `drivers/dummy_behavior.py`,
  `behaviors/{landing,resolve_conflict}.py`, `healer.py`, `ports/agent.py`
  (`AgentSpec.allowed_outcomes: tuple[str, ...]`, default `("done",)`),
  `drivers/memory.py`, `drivers/claude_cli.py` (verdict parse → string).

**Files.** `models.py`, `consumer.py`, `ports/agent.py`,
`drivers/{dummy_behavior,memory,claude_cli}.py`,
`behaviors/{landing,resolve_conflict}.py`, `healer.py`; tests updated to the
string type (`test_models`, `test_consumer`, `test_dummy_behavior`,
`test_agent_ports`, the behavior/healer tests). No behavior change yet — the two
strings behave exactly as the two enum members did.

## Package B — the workflow owns the vocabulary + hints (data model)

**Gap.** The vocabulary is derived once at `harness agent init` and frozen into
the persona (`cli._allowed_outcomes_for` → `_agent_definition_template`,
`cli.py:446-470`); it drifts from the graph. Hints have nowhere to live.

**Design.**

- `models.py`:
  - `Transition` gains `hint: str = ""` (prompt-only; `route()` never reads it).
  - `Workflow` gains `descriptions: dict[str, str] = {}` and two methods:
    `outcomes_for(step) -> tuple[str, ...]` (promoted from `cli._allowed_outcomes_for`,
    unique `on` of edges leaving `step`, definition order) and
    `description_for(step) -> str | None`.
- `drivers/fs_workflows.py` `_parse_workflow`: read an optional `"hint"` on each
  transition object; parse and validate a `descriptions` map exactly like
  `finishers` (object; every key a known step; every value a non-empty string).
  Both the read path (`.get`) and the admin write path (`write_raw`) reject a
  bad `descriptions` through the one shared contract.
- `cli.py`: `_allowed_outcomes_for` is replaced by `workflow.outcomes_for` at
  its call site in `_write_default_agents` / `_agent_init` (the derivation now
  has one home, the model). The seeded persona `allowed_outcomes` is now
  explicitly the *fallback* — a comment says so; no behavior change to `init`.

**Files.** `models.py`, `drivers/fs_workflows.py`, `cli.py`; tests
(`test_models` for the new methods + defaults, `test_fs_workflows` for `hint` /
`descriptions` parse + validation on read and admin-write).

## Package C — the behavior sources the set live (behavior + prompt)

**Gap.** `ClaudeCliBehavior` reads `spec.allowed_outcomes` (frozen) for both the
prompt and — via the runner — enforcement.

**Design.**

- `behaviors/agent.py`:
  - `__init__` gains `workflows: WorkflowRepository | None` (a **port**;
    behaviors may import ports — `test_architecture.py` allows it, forbids only
    drivers). `None` keeps the pure workflow-less path for tests that don't wire
    one.
  - `run()`: resolve `task.workflow_template` via the repository (guard
    `WorkflowNotFound` → fallback). When a workflow resolves, compute
    `derived = workflow.outcomes_for(step)` and the `{outcome: hint}` map and the
    step `description`; run the agent with `replace(self._spec,
    allowed_outcomes=derived)` so the runner enforces the live set (invariant
    #13 untouched). No workflow → use `self._spec` as-is (fallback).
  - `compose_prompt`: signature takes `outcomes: tuple[str, ...]`,
    `hints: dict[str, str]`, `description: str | None` instead of reading
    `spec.allowed_outcomes`. Render the description as role framing and the
    outcomes as an annotated choice list; the verdict block is unchanged.
- `app.py` / `cli._run`: thread the existing `WorkflowRepository` into every
  `ClaudeCliBehavior` construction (`build()` already has it in scope for the
  dispatcher). A behavior for a workflow-less catalog is constructed with
  `workflows=None`.

**Files.** `behaviors/agent.py`, `app.py`, `cli.py`; tests
(`test_agent_behavior`: derived set reaches prompt + runner spec; hint text in
the prompt; workflow-less fallback to spec; out-of-vocabulary verdict → task
fails loudly), `test_architecture` (assert the new port import is allowed and
no driver import crept in).

## Package D — an ADR and the demonstrating e2e

**Design.**

- `docs/adr/0018-workflow-owns-outcome-vocabulary.md` — Context (the enum
  blocker + the snapshot/drift bug), Decision (open string; workflow derives the
  live vocabulary; spec is the workflow-less fallback; hints are prompt-only),
  Consequences (data-driven conditional pipelines; the designer/`ui`-`backend`
  fork; router unchanged).
- An e2e (extend `test_phase3_e2e.py` or a new `test_workflow_outcomes_e2e.py`
  on the in-memory drivers): a workflow with a `plan` step forking `ui` /
  `backend`; a `FakeAgentRunner` scripted to emit `backend`; assert the task
  routes `plan → development` and never visits `designer`. A second run emitting
  `ui` visits `designer`. Proves the fork with zero router change.

**Files.** `docs/adr/0018-…md`, the e2e test.

## Final pass — docs + full suite

- CLAUDE.md: add invariant #42 (workflow owns the vocabulary; hints are
  prompt-only); update the module map note on `behaviors/agent.py` (now reads
  `WorkflowRepository`); a gotcha that `AgentSpec.allowed_outcomes` is the
  workflow-less fallback, not the primary declaration.
- `.venv/bin/pytest -q` green, including `test_smoke_git.py`.

## Execution

A first (foundation; every later package depends on the string type). Then B and
C in parallel — B is `models`/`fs_workflows`/`cli`, C is `behaviors`/`app`; they
overlap only in `models.py` (B adds methods, C imports them), so C rebases on B.
D and the docs pass last. Commits: `feat:` for A/B/C, `feat:`/`docs:` for D,
`docs:` for the final pass. Branch `claude/workflow-defined-outcomes-wvilv6`.

## Risks / call-outs

- **`test_architecture.py` may forbid `behaviors/` → `ports/workflows`.** If the
  guard is a blanket "behaviors import only a fixed port set", widen it
  deliberately (workflows is a legit behavior dependency here) and note it —
  don't smuggle the import past a stale allowlist.
- **The persona `allowed_outcomes` snapshot stays on disk.** It is now advisory
  for workflow-backed steps; a follow-up could stop writing it entirely, but
  this increment keeps it (harmless) to avoid churning every `init` fixture.
- **`claude_cli` verdict parsing** must accept any string outcome now, not a
  fixed pair — check the parser doesn't hardcode the two values (the opt-in
  `test_smoke_claude.py` covers the real shell).
