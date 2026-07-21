# ADR-0004: Pure router

Status: Accepted

## Context

The dispatcher needs to answer "given this task's current status and its last
outcome, which step comes next?" — a question fully determined by the
workflow's `transitions` and the task's own state. There is no reason this
question should ever need to read a clock, touch a file, or await an I/O call;
doing so would make the routing decision himself dependent on timing or
environment, which would make the workflow graph untestable without the very
infrastructure it's supposed to be independent of.

## Decision

`router.route()` is a pure function: given `(workflow, status, last_outcome)`
it returns the next step (or `end`, or a failure), with no side effects and no
dependency beyond `models.py`. `tests/test_architecture.py::test_router_only_
knows_models` asserts this at the import level — `router.py`'s only
`harness.*` import may be `harness.models`, nothing else, so it cannot reach
for a port, a driver, or the clock even by accident.

`END = "end"` is a reserved, explicit node in the workflow graph — not merely
"a step with no outgoing transitions." A typo in a transition's `to` field
(e.g. `"nd"`) is a routing failure the dispatcher can detect and fail loudly on,
rather than a state that silently behaves like success because it happens to
have no further edges.

## Consequences

- Every routing decision can be tested with a plain function call and a
  workflow fixture — no `FakeClock`, no in-memory queue, no async test harness
  is needed to verify a transition table.
- The router can never itself be the reason a test needs real time or a real
  filesystem, which keeps the fast unit suite fast.
- Because `route()` cannot special-case a driver's behavior, any policy that
  depends on runtime state (e.g. rate-limiting how often a step retries) has to
  live somewhere else in the pipeline — the router only ever answers the pure
  graph question.
