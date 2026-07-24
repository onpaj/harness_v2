# Workflow-defined outcomes — design (2026-07-23)

## Problem

A step's outcome vocabulary is bounded by a **closed enum**: `Outcome.DONE` and
`Outcome.REQUEST_CHANGES` (`models.py:30`). Every path that a behavior can
report through is one of exactly two values, so a workflow cannot express a
richer decision — a triage step that forks `ui` vs `backend`, a review that can
`approve` / `request_changes` / `escalate`. The routing layer is *already*
outcome-agnostic (`Transition.on` is a free string, matched by equality in
`Workflow.target`, `models.py:174`), so the enum is the sole blocker.

There is also a **duplication with a drift bug**. What a step may emit is
declared twice today:

- the workflow's transitions (`{from, on, to}`) — the edges the router follows;
- the persona's `AgentSpec.allowed_outcomes` (`ports/agent.py:29`) — the set the
  prompt lists and the runner enforces.

These are kept in sync by hand. `harness agent init` even derives the second
from the first once (`_allowed_outcomes_for`, `cli.py:446`) — but only as a
**snapshot** frozen into the persona file. When a workflow later gains an edge,
the persona's copy is stale: the agent is never told the new outcome exists, or
is told about one the graph no longer handles (an outcome with no edge silently
routes to `Finished()`, `router.py:33-35`).

## Scope

**In:**

1. Turn `Outcome` from a closed enum into an **open, validated string**.
2. Make the **workflow the live source of truth** for a step's outcome
   vocabulary, derived from its outgoing transitions — not a snapshot.
3. Add two kinds of **prompt-only hint** to the workflow: a per-outcome `hint`
   (on the transition) and a per-step `description`. Both steer the agent's
   *choice* of outcome; neither is ever read by the router or dispatcher.

**Out (deliberately deferred):**

- **Conditional/predicate routing.** "Skip the designer step for a backend
  feature" is *not* a new mechanism — it is an upstream triage step emitting
  `ui`/`backend` and the router following the matching edge (invariants #2/#4:
  a judgment is an agent outcome, never a router predicate). This design makes
  that expressible; it adds no `if` to the router.
- **A first-class `Step` object.** Steps stay derived from transitions; the
  step `description` rides in an optional map, exactly like `maxParallel` /
  `finishers`.
- Any change to the healer / resolve-conflict / workflow-less outcome bounds
  beyond keeping `AgentSpec.allowed_outcomes` as their fallback (see §Fallback).

## Design

### 1. `Outcome`: closed enum → open string

`BehaviorResult.outcome` and `AgentRun.outcome` become `str`. Everything
downstream already persists the outcome as a string via `.value` (history,
events, `last_outcome`, the queue JSON), so the data layer is unaffected —
this is a type-level change, not a format change.

`DONE = "done"` and `REQUEST_CHANGES = "request_changes"` survive as
**module-level string constants** in `models.py` (the enum's `.value`s promoted
to plain names), so `DummyBehavior`, `LandingBehavior`, `ResolveConflictBehavior`
and the healer keep referring to them by name rather than by literal.

The consumer's guard changes from an enum `isinstance` check
(`consumer.py:82`) to "the outcome is a non-empty string". This stays a
**shape** check, never a branch on the *value* — invariant #2 holds (the
consumer still decides nothing). The authoritative "is this outcome legal for
this step" check stays at the behavior/runner boundary (§3), where it already
lives.

### 2. The workflow owns the vocabulary — live, derived

Promote the existing `cli._allowed_outcomes_for` into the model as a pure
method:

```python
class Workflow:
    def outcomes_for(self, step: str) -> tuple[str, ...]:
        """Unique outcomes of the edges leaving `step`, in definition order."""
```

This is the single source of truth for what a step may emit. Because the set is
*exactly* the step's outgoing edges, a validated outcome always has a matching
edge — so termination is only ever via an explicit edge to `end`
(`{"on": "done", "to": "end"}`, already how the shipped workflows end,
`cli.py:108`). The "outcome with no edge → silent `Finished()`" footgun
disappears: an out-of-vocabulary outcome is rejected loudly at the verdict
boundary (§3), never silently treated as success. **The router is untouched**
(invariant #4 intact).

### 3. The behavior sources the allowed set from the workflow at run time

A `ClaudeCliBehavior` instance is per-step and shared across tasks, holding only
its `spec` — it does not know the workflow today. But the allowed set is
per-`(workflow, step)`, and a task carries its own `workflow_template`. So the
behavior gains a `WorkflowRepository` (a **port** — behaviors may touch ports,
never drivers) and, at run time:

1. resolves `task.workflow_template` → `Workflow` (if any);
2. computes `workflow.outcomes_for(task.status)` and the matching hints;
3. feeds that set to **both** the prompt (`compose_prompt`) **and** the runner's
   verdict enforcement — by handing the runner a spec whose `allowed_outcomes`
   is the derived set: `replace(self._spec, allowed_outcomes=derived)`. The
   `AgentRunner` is unchanged — it still enforces against `spec.allowed_outcomes`
   (invariant #13); we just compute that field live instead of reading it frozen.

So the same derived set bounds the prompt *and* the enforcement — one source,
two uses, no drift.

### Fallback: workflow-less / non-workflow paths

Not every behavior runs under a workflow: a workflow-less task carrying only a
`step` (invariant #8), the healer (a loop over `failed/`), and the resolver's
own steps run without a routable graph to derive from. For those,
`AgentSpec.allowed_outcomes` **survives as the fallback**. The rule:

- **workflow present** → `workflow.outcomes_for(step)` is authoritative; the
  spec's `allowed_outcomes` is ignored for that run (drift eliminated).
- **workflow absent** → the spec's `allowed_outcomes` (default `("done",)`).

`AgentSpec.allowed_outcomes` thus changes meaning from "the primary declaration"
to "the workflow-less fallback". `harness agent init` keeps seeding it (harmless,
now advisory) — a persona file written before this change behaves exactly as
before.

### 4. Hints — prompt-only, two kinds

Both are pure prompt data. Neither reaches the router or the dispatcher; the
`route()` function keeps reading only `from_step`/`on`/`to`.

**Outcome hint — on the transition.** A `Transition` *is* a `(from_step, on)`
pair, so it is the natural, non-redundant home for "what this outcome means":

```python
@dataclass(frozen=True)
class Transition:
    from_step: str
    on: str
    to_step: str
    hint: str = ""      # prompt-only; router never reads it
```

Parsed from an optional `"hint"` on each transition object in the workflow JSON.

**Step description — an optional map.** Not derivable from edges, so it rides in
a `descriptions: dict[str, str]` map (step → text), parsed and validated in
`_parse_workflow` exactly like `maxParallel` / `finishers` (every key a known
step). `Workflow.description_for(step) -> str | None`.

The division of labour, made explicit so the two hints don't fight:

- **persona `prompt`** = reusable "who I am" (a `designer` across every workflow);
- **workflow `description`** = "what this pipeline needs from you here";
- **transition `hint`** = "pick this outcome when …".

### 5. Prompt composition

`compose_prompt` (`behaviors/agent.py:83`) changes its inputs from
`spec.allowed_outcomes` to the workflow-derived `(outcome, hint)` pairs plus the
optional step `description`. It renders the description as role framing and the
outcomes as an annotated choice list, e.g.:

```
This step (designer): produce the UI mockups this feature needs.

Finish by choosing exactly one outcome:
  - "done": the mockups are complete
  - "request_changes": the brief is under-specified; send it back to planning
```

The machine-readable verdict block is unchanged — the agent still ends with
`{"outcome": "<one of: …>", "summary": "…"}`, and the runner still enforces the
choice is in the derived set.

## The designer example, end to end

A "development" workflow that skips UI design for backend features needs **no
new machinery** — just a richer vocabulary and a hint on the deciding step:

```json
{
  "start": "plan",
  "descriptions": {
    "designer": "produce UI mockups; only reached for user-facing features"
  },
  "transitions": [
    {"from": "plan", "on": "ui",      "to": "designer",
     "hint": "the feature has a user-facing surface"},
    {"from": "plan", "on": "backend", "to": "development",
     "hint": "purely server-side, no UI"},
    {"from": "designer",    "on": "done", "to": "development"},
    {"from": "development",  "on": "done", "to": "review"},
    {"from": "review",       "on": "done", "to": "end"},
    {"from": "review", "on": "request_changes", "to": "development"}
  ]
}
```

The skip is a **routing outcome emitted by `plan`**, not a property of the
`designer` step: a `backend` verdict never reaches the designer because the
router follows `plan --backend--> development`. The `designer` step's own
`description` only frames the designer agent's job once it *is* reached. This is
the payoff of open outcomes: a data-driven conditional pipeline, with the
routing guarantee still in the edges (deterministic) and only the *choice* of
edge steered by a hint (a judgment).

## Invariants touched

- **New invariant (proposed #42):** a step's outcome vocabulary is the
  workflow's, derived live from its outgoing transitions; `AgentSpec.allowed_outcomes`
  is only the workflow-less fallback. Hints (transition `hint`, step
  `description`) are prompt-only — the router never reads them (invariant #4
  holds).
- **#2 preserved:** the consumer's new guard is a shape check (non-empty
  string), not a branch on the outcome value.
- **#4 preserved:** `route()` is unchanged; `Transition.hint` / `descriptions`
  are invisible to it.
- **#13 preserved:** the `AgentRunner` still enforces against
  `spec.allowed_outcomes`; the behavior computes that field live.
- **#8 preserved:** router/dispatcher still decide only on `(status,
  lastOutcome)` and workflow-presence; the *behavior* (not the router) is what
  newly consults the workflow, which is a legal port dependency for a behavior.

## Testing

- `test_models.py` — `Workflow.outcomes_for` (order, dedup, unknown step → `()`);
  `Transition.hint` / `descriptions` parse and default; `Outcome` constants.
- `test_agent_behavior.py` — with a `FakeAgentRunner` and a fake
  `WorkflowRepository`: the derived set (not the spec's) reaches the prompt and
  the runner's spec; a workflow-less task falls back to the spec; an
  out-of-vocabulary verdict fails the task loudly (no silent `Finished()`).
- `test_fs_workflows.py` — `hint` on a transition and a `descriptions` map parse
  through the shared `_parse_workflow` contract; a `descriptions` entry for an
  unknown step is rejected on both the read and admin-write paths.
- `test_consumer.py` — a non-empty string outcome delivers; a non-string /
  empty outcome fails, with no branch on the value.
- `test_router.py` — unchanged behavior asserted against the widened outcome
  type (a fork on `ui`/`backend` routes correctly).
- `test_architecture.py` — `behaviors/agent.py` importing `ports/workflows` is
  allowed; it still imports no driver.
- Full suite green (`.venv/bin/pytest -q`), including the git smoke.
