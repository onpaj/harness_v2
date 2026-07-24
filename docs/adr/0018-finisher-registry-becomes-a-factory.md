# ADR-0018: The finisher registry becomes a factory; `label-issue` composes with a step's agent

Status: Accepted

## Context

ADR-0016 turned "what a step's finishing behavior is" into data: a kind name
resolved against a registry of `ConsumerBehavior` instances, `{"open-pr":
landing}` by default. Every finisher that existed at the time *replaced* the
step's own behavior outright — `land` never runs an agent, it only lands.

The triage-Process scenario needs the opposite shape: a `product-manager`
persona (an ordinary `ClaudeCliBehavior`, built from a catalog `AgentSpec`)
must run first and return a verdict, and only *then* should a label be applied
to the task's source GitHub issue based on that verdict. The finisher needs to
**wrap** the step's own behavior, not replace it — a shape ADR-0016's
`dict[str, ConsumerBehavior]` registry (one fixed instance per kind, resolved
before the step's own behavior is even considered) cannot express.

Two further constraints, discovered while grounding this change against the
actual codebase:

- `label-issue`'s only reason to exist is one driver verb,
  `GithubClient.add_label` — the task's own scope explicitly rejects a new
  port for it. But `test_behaviors_import_only_ports_not_drivers` forbids
  anything under `behaviors/` from importing a driver, and this would be the
  first `ConsumerBehavior` with that dependency shape.
- A finisher factory that eagerly builds "the behavior a step would otherwise
  get" — via `catalog.get(step)` — breaks the single most common finisher
  binding in the system: the landing step. `_write_default_agents` skips
  writing an agent file for `LANDING_STEP` on the assumption that `land` never
  needs one (it's always fully replaced by `open-pr`). Computing it
  unconditionally for every bound step, as an initial implementation attempt
  did, raises `AgentNotFound` for exactly that ordinary case — caught by the
  existing `test_resolver_task_flows_through_resolve_and_land_to_done`.

## Decision

- `Workflow.finishers` becomes `dict[str, FinisherBinding]`, where
  `FinisherBinding(kind: str, config: dict = {})` is a frozen dataclass.
  `_parse_workflow` accepts a step's binding as either a plain string
  (`"open-pr"` → `FinisherBinding(kind="open-pr")`, unchanged meaning for every
  existing workflow file) or an object (`{"kind": "label-issue", "labels":
  {...}}` → everything but `"kind"` becomes opaque `config`). `config` is not
  validated at parse time — the same posture as `_parse_action` not validating
  check-specific params; a given finisher kind's factory validates its own
  config, at `build()` time.
- `app.build()`'s finisher registry becomes `dict[str, Callable[[str, dict,
  Callable[[], ConsumerBehavior]], ConsumerBehavior]]` — kind → **factory**.
  A factory receives the step name, that binding's `config`, and a **zero-arg
  thunk** that lazily builds the "inner" behavior `behavior_for` would
  otherwise have returned for that step (the real `ClaudeCliBehavior` from
  `catalog.get(step)` when a catalog is wired, `work` otherwise). The thunk is
  lazy — not an already-built `ConsumerBehavior` — specifically because a
  factory that never calls it (e.g. `open-pr`'s, which ignores all three
  arguments and always returns the same `landing` singleton) must not trigger
  `catalog.get(step)` at all. This is what keeps the landing step's
  no-agent-file convention intact.
- `label-issue`'s factory *does* call the thunk: it runs the step's own
  behavior first (the persona, unmodified), then wraps the `BehaviorResult` —
  reading `result.outcome.value` to look up a label, calling
  `GithubClient.add_label` when one is mapped, and returning the original
  result untouched either way (routing is never affected — invariant #8).
- `LabelIssueBehavior` lives at `drivers/label_issue.py`, not `behaviors/`. It
  is the first `ConsumerBehavior` under `drivers/` — a deliberate, narrow
  exception to the `behaviors/` vs `drivers/` file-location symmetry
  established by `LandingBehavior`/`ClaudeCliBehavior`/`ResolveConflictBehavior`,
  made because it depends on `GithubClient` (a driver with no port) rather
  than a port. `drivers/` importing `drivers/` is already precedented
  (`github_issues_check.py` imports `GithubClient` the same way). This is not
  a signal that `behaviors/` is deprecated — every other finisher/behavior
  stays exactly where it is.
- The cross-workflow conflict check (two served workflows binding the same
  step) now compares the *whole* `FinisherBinding` — kind and config — not
  just the kind string, since two workflows binding the same step to
  `label-issue` with different `labels` maps is just as much a wiring
  contradiction as binding it to two different kinds.
- The concrete `GithubClient` the `"label-issue"` factory closes over is
  supplied by `cli.py`'s `_run` (one client, built once, threaded into both
  `_process_sources` and the finisher registry — the same "one client per
  wiring site" shape every other GitHub-touching helper in `cli.py` already
  follows). `app.py` never imports `GithubClient` and gains no new parameter
  shape beyond the widened `finishers` type.

## Consequences

- A finisher kind can now be built either way — full replacement (`open-pr`)
  or wrap-and-act (`label-issue`) — from the same registry mechanism, with no
  branch in `behavior_for` on either the step's name or the finisher's kind
  beyond the dict lookup itself.
- `Workflow.finisher_for` returns `FinisherBinding | None`. It has exactly one
  production caller today (`app.py` walks `.finishers.items()` directly, never
  calls it) — confirmed by grep — so no back-compat shim was introduced;
  widening its return type has zero runtime blast radius, only the two tests
  that call it directly.
- `app.build()`'s `finishers` parameter is a breaking signature change (kind →
  behavior becomes kind → factory). Every existing caller supplying a bare
  `ConsumerBehavior` (`tests/test_app.py`) moves to a one-line lambda ignoring
  all three arguments — the only migration this ADR requires downstream.
- A future finisher that needs the inner behavior's result at construction
  time rather than by wrapping `run()` is still expressible: the factory
  receives `config` and can shape a new wrapper class per binding, exactly as
  `label-issue` does.
