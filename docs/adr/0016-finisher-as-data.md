# ADR-0016: Finisher as data

Status: Accepted

## Context

"Landing is a step, not magic" (ADR-0009) settled *where* finishing happens: an
ordinary workflow node that can fail into `failed/`. But *what* finishing does
was still hardwired — `app.build()`'s `behavior_for()` branched on the step
name (`LANDING_STEP = "land"` → `LandingBehavior` → `Forge.open_pull_request`),
so there was exactly one finisher in the system, selected by naming convention.
Adding "create a file" or "call an API" as a finishing action would have meant
a new behavior class *plus a new name-keyed branch* — precisely the shape
invariant #14 forbids for personas ("no branch on the agent's name"). The
finisher slot was the one place the codebase contradicted its own modularity
principle.

## Decision

Mirror ADR-0007 (persona as data): a step's finishing behavior is chosen by
**data resolved against a registry**, not by a step's name.

- `Workflow` gains `finishers: dict[str, str]` (step name → finisher kind),
  parsed from an optional `"finishers"` key in the workflow JSON and validated
  by the same shared `_parse_workflow` contract as `maxParallel` — every key a
  known step, every value a non-empty string — so the read path and the admin
  write path reject a bad binding identically. `Workflow.finisher_for(step)`
  returns the kind or `None`.
- `app.build()` gains `finishers: dict[str, ConsumerBehavior] | None` — the
  **finisher registry**, kind → behavior. The default registry is
  `{"open-pr": landing}`; a caller-supplied dict is merged over it. `Forge` is
  thereby demoted to the driver behind the `open-pr` kind.
- `build()` merges every *served* workflow's `finishers` into one step → kind
  map. A conflict (two served workflows binding the shared step queue to
  different kinds) and an unknown kind both raise `ValueError` at build — fail
  fast, never lazily from `behavior_for` once tasks are flowing. When no
  served workflow binds `landing_step`, the map defaults it to `"open-pr"` —
  full backward compatibility: a workflow file written before this feature
  behaves exactly as before.
- `behavior_for(step)` resolves step → kind → registry; the name-keyed
  `if step == landing_step` branch is deleted. The `RESOLVE_STEP` branch stays
  untouched — the resolver is a conflict-fixing worker, not a finisher; folding
  it into the registry is a candidate follow-up, not part of this decision.

## Consequences

- A new finishing action ("create a file", "call an API") is a registry entry
  handed to `build()` plus a `finishers` binding in the workflow JSON — never a
  new branch in `behavior_for`, and never a reserved step name.
- A workflow author can bind *any* step name (e.g. `"publish"`) to `"open-pr"`
  and it lands through the forge, so the vocabulary of step names is finally
  free of the `"land"` convention — while every existing workflow keeps landing
  through it unchanged.
- Misconfiguration surfaces at build, not mid-run: an unknown kind or a
  cross-workflow conflict stops the harness before a single task moves, the
  same posture as a missing agent spec failing fast through `catalog.get`.
- The `landing_step` parameter and the `LANDING_STEP` constant survive as the
  backward-compatibility default only — they no longer select a behavior by
  name anywhere.
