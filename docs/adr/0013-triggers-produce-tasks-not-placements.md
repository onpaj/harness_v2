# ADR-0013: Triggers produce tasks, never queue placements

Status: Accepted

## Context

Tasks are born by hand (`harness submit`) and from `TaskSource.poll()` — a GitHub
issue labelled for pickup, or a "dirty" PR the mergeability watcher spots. We want
a third, generic shape: a **trigger** that fires on a schedule ("every hour, check
a condition; if it holds, create a task") aimed at whichever queue the operator
names. The tempting reading of "create a task into any queue" is a producer that
writes straight into a step queue — which would put a second author on task
placement, the decision ADR-0002 and ADR-0004 reserve for the router/dispatcher
(`CLAUDE.md` invariant #3: the dispatcher changes status; invariant #8: it decides
on `(status, last_outcome)` + workflow-presence alone, never on who made the task).

A second question was whether a scheduled trigger — which has no external item to
reflect its state onto — should be forced through `TaskSource`'s full three-verb
contract (`poll`/`report_progress`/`finish`, ADR-0010), or whether that contract
should be split.

## Decision

A **trigger is a `TaskSource` that produces tasks and reflects nothing outward**,
and it **never chooses a queue**. It hands a fresh `Task` to the inbox carrying a
`workflow_template` (flows from that workflow's `start`) **or** a `step` (a
workflow-less task the dispatcher routes straight to that step via
`router.py`'s `status is None` path). Placement stays the dispatcher's, so
invariants #3 and #8 hold unchanged: "target any queue" is expressed as a
workflow-less task naming a `step`, not as a producer reaching past the
dispatcher. This reuses the shape `GithubTaskSource` already exposes (`workflow=`
**or** `step=`).

`TaskSource` stays whole (ADR-0010, invariants #18–#20 unchanged). We add a
convenience base `Trigger(TaskSource)` whose `report_progress`/`finish` are
concrete no-ops, so a schedule- or condition-trigger implements only `poll()`. A
`Trigger` joins the same `sources` list, gets the same `SourcePoller` and
`dedup_key` deduplication, and is listed-and-ignored by `SourceReflectorSink`
because it stamps no matching `data.source` — the identical path that already
ignores a `harness submit` task. No new port, no change to `SourcePoller`,
`dispatcher`, `consumer`, or `route()`.

A scheduled trigger **owns its cadence** by gating `poll()` on the interval
bucket `floor(now / interval)` read from the `Clock`, returning `[]` cheaply
between fires. The shared `source_interval` becomes mere polling granularity, and
the gate — reading only `Clock` — is `FakeClock`-testable and immune to
`SourcePoller`'s `sleep(0)` fast-path re-firing. Its `dedup_key` includes that
bucket (or, for `per-state` dedup, an observed `state_key`), so the existing
seeded-`_seen` mechanism yields **at-most-once per interval across restarts** with
a non-constant key — the exactly-once guarantee of ADR-0010 applied to recurring
work.

Triggers are declared as data (`triggers/*.json`, read by
`FilesystemTriggerRepository`), mirroring `agents/*.json`. A file declares an
instance of a built-in trigger *type*: schedule, target, and a *named* check.
The check itself is code (a `Check` in a `CheckRegistry`) — the honest boundary
that ADR-0007 draws for personas (data declares which and how often; code
supplies what it checks).

## Consequences

- Adding a scheduled or condition trigger is a new driver behind the existing
  `TaskSource` port plus a `triggers/*.json` file — no change to the routing core,
  `SourcePoller`, or `SourceReflectorSink`, exactly as ADR-0010 promised for a new
  source kind.
- A pure trigger no longer carries dead projection code: `Trigger`'s no-op verbs
  make `poll()` the only method a schedule/condition author writes.
- The dispatcher remains the single authority on placement; a triggered task is
  indistinguishable to the router from a hand-submitted one, so no new decision
  branch appears in `route()`.
- v1 dedup is *at-most-once per interval* (or per observed state), **not**
  *suppress-while-in-flight*: `_seen` remembers "ever ingested", not "currently
  open". A trigger that must not duplicate a still-unresolved task needs the
  poller/trigger to consult live task state — a deliberate follow-up, not covered
  here.
- The cadence lives in the trigger, not in a new loop or a per-source interval
  knob, so `Harness.run()`'s shape is untouched; a per-source `poll_interval` hint
  remains available as a later optimization for truly idle long-interval triggers.
