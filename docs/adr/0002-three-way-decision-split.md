# ADR-0002: Three-way split of decision-making

Status: Accepted

## Context

A task's journey through a workflow has three distinct questions: *what
happened* at this step, *where does the task go next*, and *how does the task
get delivered there*. Conflating them — for instance, letting the consumer
itself decide the next queue based on the outcome it just received — would
scatter routing logic across every behavior and make the workflow graph
implicit in code rather than explicit in `transitions`.

## Decision

The three questions are answered by three separate, non-overlapping roles.
`ConsumerBehavior.run()` says *what happened*, returning a `BehaviorResult
(outcome, summary)` — it has no notion of queues or steps beyond the one it was
asked to perform. The dispatcher, reading `(status, last_outcome)` through
`route()`, decides *where the task goes next* (a step name, or `end`). The
consumer *delivers*: it invokes the behavior, writes the outcome, and moves the
task — it makes no decision that depends on the outcome's value.

That last part is enforced, not just documented:
`tests/test_architecture.py::test_consumer_has_no_branch_on_outcome_value`
parses the AST of `consumer.py` and fails if any comparison anywhere in the
module (not just inside the `Consumer` class — the check walks the whole
module so a branch can't be smuggled out into a free function) derives from an
outcome value, including through an import alias.

## Consequences

- Adding a new outcome value, or a new transition on an existing one, is purely
  a workflow-definition change (`transitions` in the workflow JSON) plus a
  routing rule in `router.py` — `consumer.py` never needs to change.
- A behavior can be swapped (dummy → real agent) without the consumer or
  dispatcher noticing, because neither cares *how* the outcome was produced,
  only that it is one of the outcomes the workflow knows how to route.
- The one exception, where the consumer itself decides a terminal outcome, is
  the delivery failure case covered by invariant #3 (see ADR-0004's neighbor,
  the dispatcher's `_fail` symmetry) — that is not a branch on outcome *value*,
  it is the consumer reacting to its own inability to deliver one at all.
