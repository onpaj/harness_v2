# ADR-0015: A Process is a compile-time authoring aggregate

Status: Accepted

## Context

Generic triggers (ADR-0014) gave us schedule-driven task creation as data:
`triggers/*.json`, each flatly mixing a cadence (`interval`), a condition
(`check` + `params`) and a destination (`target`). Two gaps remained against the
operator's mental model:

1. **No single object ties the pieces together with named roles.** The operator
   thinks in terms of *"a process: on this trigger, run this action, send the
   result to this queue, and reflect its state to this place."* A flat trigger
   file expresses three of those four roles, unnamed and unstructured, and omits
   the fourth (the outbound reflection target) entirely.
2. **The outbound side is welded shut.** How a task's progress/outcome is
   reflected back to its origin lives inside a `TaskSource` driver
   (`GithubTaskSource` relabels the issue). There is no way to say "ingest from
   GitHub, reflect to Slack" — source and destination are the same object by
   construction.

The temptation is to make "Process" a first-class runtime entity the orchestrator
knows about. That would put a new author on task placement and state (the
decisions ADR-0002/ADR-0004 reserve for the router/dispatcher) and would grow a
god-object the core branches on — breaking the layering `test_architecture.py`
guards.

## Decision

A **Process is a compile-time authoring aggregate, not a runtime object.** A
`processes/*.json` file names four distinct roles —

- **trigger** (cadence: `interval`),
- **action** (a named `Check` + `params` — the inbound producer),
- **target** (a workflow **or** a step),
- **sink** (the outbound reflection destination),

— and `FilesystemProcessRepository` **compiles each Process into a
`ScheduledTrigger`** (ADR-0014), a `TaskSource` that joins the existing `sources`
list. The orchestration core (`dispatcher`, `consumer`, `router`,
`source_poller`) never imports or names "process"; a Process is `cli.py`'s
wiring turned into declarative data. Placement stays the dispatcher's (invariants
#3/#8/#35): a Process targets a workflow or a step, and `route()` places the
produced task — no producer reaches past the dispatcher. `TaskSource` stays whole
(ADR-0010, invariants #18–#20 unchanged).

The **`sink` field is a forward-compat seam.** It is parsed and validated but
restricted to *absent* or `{"kind": "none"}` in this increment: v1 processes are
fire-and-forget (their `ScheduledTrigger` stamps no `data.source` and reflects
nothing, like every trigger today). The field exists now so that keeping
**source ≠ destination** open — GitHub in, Slack out — is a *driver addition and
a flipped field*, never a schema migration. When a real sink lands it routes
through `SourceReflectorSink` on a destination identity (a `data.sink` slot)
that **defaults to `source.kind`**, so same-origin processes need declare
nothing; and a stateful sink (create-a-Slack-message-then-edit-it) may return a
persistable handle, refining invariant #21 from "report twice is a no-op" to
"convergent." No `Reflector` port is built here — a port whose only
implementation is a no-op is over-engineering; the seam is the `sink` field plus
this recorded shape.

`triggers/*.json` is unchanged: a Process compiles to the *same*
`ScheduledTrigger` a bare trigger file does, so the two surfaces coexist — the
Process is the richer primary authoring surface, the trigger file the low-level
primitive it is built from.

## Consequences

- Adding the Process layer is a new driver (`FilesystemProcessRepository`) plus
  `processes/*.json` files and a `cli.py` wiring helper — **no change to the
  routing core, `SourcePoller`, `SourceReflectorSink`, `build()`'s signature, or
  any port.** The same "new capability = a new file behind an existing port"
  story as ADR-0010/ADR-0014.
- The operator gets one object per automation with named roles, and can define
  several independent processes — the "tie it all together" surface.
- The outbound half of the round-trip has a declared home (`sink`) before it has
  a driver, so the GitHub→Slack split is a later increment that changes no schema
  and no invariant — only registers a reflector and flips `sink.kind`.
- Two deliberately-deferred seams are recorded, not hidden: the **`github-issues`
  action** (a source-bearing `Check` needs a `GithubClient` injected into a
  widened factory) and the **reflector registry** (the real sink drivers). Both
  are additive against this increment.
- Redundancy cost: a v1 Process and a bare trigger file can express the same
  fire-and-forget automation two ways. Accepted deliberately — the Process's
  nested roles and `sink` slot are the structure the future increments hang on,
  and folding `triggers/*.json` into `processes/` is left as a later cleanup
  rather than a churn on a just-shipped surface.
