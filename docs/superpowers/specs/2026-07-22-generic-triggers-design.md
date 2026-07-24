# Generic triggers — schedule- and condition-driven task creation

Status: draft
Date: 2026-07-22

## Goal

Today a task is born in exactly two ways: a human runs `harness submit`, or a
`TaskSource.poll()` turns an outside item into a task (a GitHub issue labelled
for pickup; a "dirty" PR the mergeability watcher notices). We want a **third,
generic shape**: a **trigger** that fires on a *schedule* — "every hour, check
some condition; if it holds, create a task" — targeting whichever workflow or
step the operator names.

The thesis of this spec is that **we already have the mechanism** and should not
build a new subsystem. A trigger is a `TaskSource` that produces tasks and
reflects nothing back outward. `GithubMergeabilityWatcher` is already exactly
this shape — poll, evaluate a condition (`pr.mergeable_state == "dirty"`), emit a
`Task` — so a scheduled trigger is a **new driver behind an existing port**, plus
three small, honest additions:

1. a `Trigger` base that makes the outward-projection verbs optional (a pure
   trigger has nothing to project onto);
2. a **cadence** that a trigger owns itself, gated on the `Clock`, so different
   triggers fire at different rates without a shared global interval;
3. **triggers-as-data** — `triggers/*.json`, mirroring `agents/` and `workflows/`
   — declaring instances of built-in trigger *types*, with genuinely bespoke
   conditions staying as small code drivers registered by name.

This is a POC-level feature in the phase spirit: the built-in checks are thin,
the tests run on `FakeClock` with no real waiting, and a live condition is a swap
of the check driver.

### What's new

- The `Trigger` base class in `ports/source.py`: a `TaskSource` whose
  `report_progress`/`finish` are concrete no-ops. A projection-less trigger
  subclasses it and implements only `poll()`.
- `ScheduledTrigger(Trigger)` in `drivers/scheduled_trigger.py`: composes an
  `interval` (data) with a `Check` (code) and a `target` (workflow **or** step).
- The `Check` protocol + a small `CheckRegistry` of built-in checks
  (`always`, and one real example, e.g. a disk/threshold check), so a JSON-declared
  trigger names its check by string.
- `FilesystemTriggerRepository` in `drivers/fs_triggers.py`: reads `triggers/*.json`
  and constructs `ScheduledTrigger`s, exactly as `FilesystemAgentCatalog` reads
  `agents/*.json`. `harness init` writes an empty `triggers/` (or a commented
  example).

### What's out of scope

- **A real scheduler daemon (cron), webhooks, distributed leases.** A trigger is
  still a plain poll tick; the cadence is a clock-gate inside `poll()`.
- **"Don't re-fire while an equivalent task is still open."** v1 dedup is
  *at-most-once per interval* (see Dedup below), not *suppress-while-in-flight*.
  The latter needs the poller to consult live tasks and is noted as a limitation.
- **A trigger admin UI.** Triggers are data on disk; a write-side admin port
  (like `AgentAdmin`/`WorkflowAdmin`) is a clean follow-up, not part of this spec.

## Core thesis: a trigger produces tasks; it never places them

A trigger's whole job is to answer *"is there new work?"* and, if so, hand a
`Task` to the inbox. It **never** chooses a queue. Placement stays the
dispatcher's sole authority (invariant #3): a fresh task (`status is None`) is
routed by the pure `route()` function —

- with a `workflow_template` → into that workflow's `start` step
  (`router.py:16-17`);
- **workflow-less, with `step` set → into exactly that step**
  (`router.py:18-20`).

So the operator's "create a task to any queue" is expressible **without any
producer touching a queue**: the trigger picks *which pipeline* (a workflow) or
*which single entry step* (workflow-less), and the dispatcher does the actual
placement. This is not a new capability — `GithubTaskSource` already accepts
`workflow=` **or** `step=` and emits accordingly (`cli.py:515-531`). The
scheduled trigger reuses that exact `target` shape.

This is the answer to the "any queue" question from the brainstorm, and it keeps
invariants #3 and #8 intact: nobody but the dispatcher decides where a task goes,
and it decides on `(status, last_outcome)` + workflow-presence alone — never on
who created the task or why.

## The `Trigger` base: projection is optional

`TaskSource` bundles three verbs because GitHub genuinely does all three — a
label on the issue reflects the task's state back outward. A **scheduled
condition-trigger has no external item to project onto**: "if disk > 80%, make a
cleanup task" leaves nothing to relabel. Forcing it to implement two no-op
methods is the only friction, and it is cosmetic — `SourceReflectorSink` already
routes projection by `kind` and silently ignores a task whose `data.source.kind`
matches no adapter, so a projection-less trigger is *already* harmless today.

We resolve the cosmetic friction with a base class, **keeping `TaskSource` whole**
(invariants #18–#20 and their ADR-0010 wording are untouched):

```python
class TaskSource(ABC):
    kind: str
    @abstractmethod
    def poll(self) -> list[Task]: ...
    @abstractmethod
    def report_progress(self, task: Task, progress: Progress) -> None: ...
    @abstractmethod
    def finish(self, task: Task, result: FinishResult) -> None: ...

class Trigger(TaskSource):
    """A TaskSource that produces tasks but reflects nothing back outward.

    poll() stays abstract; the two projection verbs default to no-ops, so a
    schedule- or condition-trigger implements only poll(). It is still a
    TaskSource: SourcePoller ingests it and SourceReflectorSink lists it (and
    ignores it — a Trigger stamps no matching `data.source`).
    """
    def report_progress(self, task: Task, progress: Progress) -> None:
        return None
    def finish(self, task: Task, result: FinishResult) -> None:
        return None
```

Nothing else changes: a `Trigger` goes into the same `sources` list, gets the
same `SourcePoller`, the same `dedup_key` dedup, the same inbox. `app.py` and
`cli.py` treat it as a `TaskSource`. `GithubTaskSource`/`GithubMergeabilityWatcher`
stay full `TaskSource`s — they reflect state and must keep implementing all three
verbs.

## `ScheduledTrigger`: interval × check × target

```python
class ScheduledTrigger(Trigger):
    def __init__(self, *, name, clock, interval, check, target,
                 dedup="per-interval"):
        self.kind = f"scheduled:{name}"          # unique routing key per trigger
        ...

    def poll(self) -> list[Task]:
        now = self._clock.now()
        if not self._due(now):        # clock-gate: not this interval's turn yet
            return []
        observations = self._check.evaluate()    # code: 0..N reasons to fire
        return [self._task_for(obs, now) for obs in observations]
```

- **`interval`** — a duration (`"1h"`, `"30m"`, `"24h"`), parsed once at load.
- **`check`** — a `Check` (code): `evaluate() -> list[Observation]`. Empty list =
  condition not met, no task. Each `Observation` may carry a `state_key` (for
  dedup, see below) and extra `data` for the task. The trivial built-in
  `always` returns a single empty observation — "fire every interval,
  unconditionally".
- **`target`** — `{"workflow": name}` **or** `{"step": name}`, turned into the
  task's `workflow_template` / `step` respectively.

The condition is code because "check disk usage" / "count open PRs" / "call an
API" cannot live in JSON. The *schedule and wiring* are data; the *check* is a
named driver — the same split as personas (invariant #14: the persona is data,
the runner is code).

## Cadence: gated on the clock, not on the loop

There is **no new loop and no per-source interval knob**. A `ScheduledTrigger`
owns its cadence internally by comparing `self._clock.now()` against the current
**interval bucket** (`floor(now / interval)`). Between fires `poll()` returns `[]`
cheaply — a clock read and an integer compare, no I/O — so the existing
`_source_loop` (`app.py:281`) can keep waking it on the shared `source_interval`
(default 30s) as nothing more than *polling granularity*: how soon after an
hour boundary the trigger actually fires. Firing every hour with a 30s wake means
worst-case 30s latency and 119 cheap empty polls in between — completely fine.

This is deliberately robust to the loop's backoff shape. `SourcePoller.tick()`
sleeps `source_interval` only when a tick ingested nothing (`app.py:284-288`);
after it *does* ingest, it loops with `sleep(0)` and polls again immediately.
A naive "fire whenever polled" trigger would then re-fire in a tight loop. The
clock-bucket gate makes that impossible: the moment it fires bucket *N*, the next
`poll()` sees the same bucket *N* and returns `[]` until the wall clock crosses
into *N+1*. Because the gate reads only `Clock`, it is fully `FakeClock`-testable
with no real waiting (invariant: unit tests never sleep in real time).

*(A per-source `poll_interval` hint on the loop — so a truly idle hourly trigger
isn't even woken every 30s — is a possible later optimization. It is not needed
for correctness and is left out of v1 to avoid touching the run loop's shape.)*

## Dedup: at-most-once per interval, reusing `dedup_key`

A scheduled trigger firing hourly produces **fresh work each time** — so unlike a
GitHub issue, its `dedup_key` must *not* be constant, or `SourcePoller._seen`
would suppress every fire after the first, forever. The key must, however, be
*stable within one fire* so a restart mid-interval doesn't double-fire. Both fall
straight out of the existing machinery by keying on the **interval bucket**:

```python
dedup_key = dedup_key(self.kind, target, bucket, observation.state_key)
#   self.kind        -> "scheduled:<name>"       (this trigger)
#   bucket           -> floor(now / interval)    (this period)
#   observation.state_key (optional)             -> the observed thing, if any
```

This gives **at-most-once per interval across restarts for free**, using the
same persisted-`dedup_key` + seeded-`_seen` mechanism that makes GitHub ingestion
exactly-once (ADR-0010): the poller seeds `_seen` from every task on disk at
startup, so a bucket already fired — its task sitting in any queue, done, failed
or archived — is never fired again.

Two dedup strategies, selected per trigger:

| `dedup` | key includes | semantics |
|---|---|---|
| `per-interval` (default) | bucket | one fire per period; `always` uses this |
| `per-state` | `observation.state_key`, **no bucket** | fire once per distinct observed state, re-fire when it changes — the mergeability watcher's `head_sha` pattern, generalised |

**Known limitation (out of scope for v1):** neither strategy is
*suppress-while-in-flight*. `_seen` remembers "ever ingested", not "currently
open", so `per-state` will re-fire if the same state recurs after its task has
terminated, and `per-interval` cannot coalesce a still-unresolved backlog. A
"don't create a duplicate while a prior task is still non-terminal" mode would
need the poller (or the trigger) to consult live task state; that is a deliberate
follow-up, called out here so it isn't mistaken for covered. Also minor:
`_seen` grows by one key per fire within a long run (hourly ≈ 8.7k/yr,
negligible; it is re-seeded from on-disk tasks each restart, so archived/cleaned
buckets drop out).

## Triggers as data: `triggers/*.json` + a check registry

Mirroring `agents/*.json` (`FilesystemAgentCatalog`) and workflow files:

```json
{
  "kind": "scheduled",
  "name": "nightly-maintenance",
  "interval": "24h",
  "check": "always",
  "target": { "workflow": "maintenance" }
}
```

```json
{
  "kind": "scheduled",
  "name": "disk-pressure",
  "interval": "1h",
  "check": "disk-threshold",
  "params": { "path": "/", "percent": 80 },
  "target": { "step": "cleanup" },
  "dedup": "per-state"
}
```

- **`FilesystemTriggerRepository`** reads the directory, validates at load
  (unknown `check` name, malformed `interval`, `target` naming neither a served
  workflow nor a known step → fail fast at build, like a missing agent spec), and
  builds one `ScheduledTrigger` per file.
- **`CheckRegistry`** maps `check` string → `Check` factory (`params` passed in).
  Built-ins ship in code; a bespoke condition is a new factory registered by name
  — data declares *which* check and *how often*, code supplies *what it checks*.
- The JSON declares an **instance of a built-in type**. This is the honest
  boundary: a cron schedule is data, a condition is code. Do not pretend the
  whole condition can be JSON.

## Wiring (`app.py` / `cli.py`)

- **`build()`** needs no new parameter. `ScheduledTrigger`s are `TaskSource`s;
  they join the existing `sources: list[TaskSource]`. Every downstream piece —
  `SourcePoller` per source, `SourceReflectorSink(sources)`, the source loops in
  `Harness.run()` — already handles them. (The reflector will list a `Trigger`
  and never call it back, because a `Trigger` stamps no matching `data.source`.)
- **`cli.py`** gains a `_scheduled_sources(root)` helper that reads
  `triggers/*.json` via `FilesystemTriggerRepository` and appends to the same
  `sources` list already assembled from `_github_sources` + `_mergeability_sources`
  (`cli.py:1250-1251`). Absent/empty `triggers/` → no scheduled sources, the
  harness runs exactly as before.
- **`harness init`** writes an empty `triggers/` (or a single commented example),
  next to where it already writes `agents/` and `repos.json`.

## Model & UI: unchanged

- **`Task` does not change.** A scheduled trigger sets `workflow_template`/`step`
  and a `dedup_key` — all existing fields. It stamps **no** `data.source` (there
  is nothing to reflect onto), so the outward projection ignores it by the same
  `_mine()` path that ignores a `harness submit` task. If a trigger wants its
  provenance visible, it may write a non-routing `data.trigger` note; the router
  and dispatcher never read `data` (invariant #8), so this is inert.
- **The board does not change.** A triggered task appears in `todo` and flows
  like any other. (A "created by trigger X" chip on the card is an optional UI
  follow-up.)

## Error states

| Situation | Detection | Where |
|---|---|---|
| `check.evaluate()` raises (disk read fails, API down) | exception out of `poll()` | `SourcePoller.tick` catches it, emits `source_error`, tick returns False, loop retries next interval — identical to a GitHub `poll()` failure |
| malformed `interval` / unknown `check` / `target` names no known workflow or step | validated in `FilesystemTriggerRepository` at build | fail fast at `harness run` start, before the loop — like a missing agent spec |
| trigger fires but its `target` workflow is unserved this run | `route()` / `ServedWorkflowRepository` at dispatch | task fails into `failed/` with a clear reason, exactly as a hand-submitted task naming an unserved workflow does |

## Code structure (additions)

```
src/harness/
  ports/
    source.py               # + Trigger base (no-op report_progress/finish)
    triggers.py             # Check (Protocol), Observation, CheckRegistry
  drivers/
    scheduled_trigger.py    # ScheduledTrigger(Trigger): interval x check x target
    checks.py               # built-in Checks (always, disk-threshold, ...)
    fs_triggers.py          # FilesystemTriggerRepository (triggers/*.json)
  cli.py                    # _scheduled_sources(); harness init writes triggers/
```

No change to `dispatcher.py`, `consumer.py`, `router.py`, `source_poller.py`,
`models.py`, or `app.py`'s `build()` signature.

## Invariants — new/refined

They extend the list in `CLAUDE.md` (currently 33), they don't replace it.

34. **A trigger produces tasks, never queue placements.** A `Trigger` (schedule-
    or condition-driven) hands a fresh `Task` to the inbox with a `workflow_template`
    **or** a `step`; the dispatcher alone places it (`route()`, invariants #3/#8).
    No trigger writes into a step queue. The "target any queue" capability is a
    workflow-less task carrying `step`, not a producer reaching past the dispatcher.
35. **A `Trigger` is a `TaskSource` that reflects nothing outward.** It implements
    only `poll()`; `report_progress`/`finish` are inherited no-ops. It stamps no
    `data.source`, so `SourceReflectorSink` lists it and ignores it — the same path
    that ignores a `harness submit` task. `TaskSource` stays whole (invariants
    #18–#20 unchanged); `Trigger` is a convenience base, not a new port.
36. **A scheduled trigger owns its cadence via the `Clock`, not via a loop.**
    `poll()` gates on the interval bucket `floor(now / interval)` and returns `[]`
    cheaply between fires; the shared `source_interval` is only polling
    granularity. The gate reads solely `Clock`, so it is `FakeClock`-testable and
    survives the poller's `sleep(0)` fast-path without re-firing.
37. **A scheduled trigger's `dedup_key` is bucket-keyed, giving at-most-once per
    interval across restarts.** The key includes `floor(now / interval)` (and, for
    `per-state` dedup, an observed `state_key` instead), so the existing
    `SourcePoller._seen` seeding makes one period yield one task even across a
    restart — the same exactly-once mechanism as GitHub ingestion (ADR-0010),
    with a non-constant key.

## Completion check

The feature is done when:

1. `ScheduledTrigger(interval="1h", check=always, target={"workflow": "wf"})`
   driven by a `FakeClock`: at *t=0* `poll()` returns one task; advancing the
   clock by <1h and polling again returns `[]`; advancing past the hour boundary
   returns exactly one new task. No real sleeping in the test.
2. A restart mid-interval (re-seed `_seen` from the on-disk task) does **not**
   re-fire that interval's task — dedup by bucket holds across restart.
3. A `per-state` trigger whose `Check` reports the same `state_key` twice in one
   run fires once; when the `state_key` changes it fires again.
4. A `Trigger` in the `sources` list is polled by `SourcePoller` and **never**
   receives a `report_progress`/`finish` call that does anything (it has no
   matching `data.source`); a `harness submit` task is likewise untouched.
5. A triggered workflow-less task with `target={"step": "cleanup"}` is placed by
   the dispatcher into `cleanup/` via `route()`'s workflow-less path — no producer
   wrote into a step queue.
6. `FilesystemTriggerRepository` fails fast at build on a malformed `interval`,
   an unknown `check`, or a `target` naming neither a served workflow nor a known
   step.
7. `harness run` with an empty/absent `triggers/` behaves exactly as today (no
   scheduled sources); with a `triggers/*.json` present it wires the trigger with
   no other change.
8. Architecture tests still pass: `dispatcher`/`consumer` import neither
   `ports.source` nor `ports.triggers`; `ScheduledTrigger`/`FilesystemTriggerRepository`
   are touched only by `cli.py`/`app.py` wiring; `SourcePoller` still imports only
   ports.

## Addendum (2026-07-23): cron cadence supersedes the "out of scope" line above

The "What's out of scope" section above says *"A real scheduler daemon (cron),
webhooks, distributed leases [are out of scope]. A trigger is still a plain
poll tick; the cadence is a clock-gate inside `poll()`."* That line is now
superseded for the *expression* only: a trigger's cadence may be a standard
5-field cron expression (`"0 6 * * 1"`) as an alternative to an interval
duration, evaluated by a stdlib-only parser (`parse_cron`/`CronSchedule`,
`ports/triggers.py`). The architecture this spec describes is otherwise
unchanged — still a plain poll tick, still a clock-gate inside `poll()`, still
no new loop, port, or daemon. See ADR-0018 for the full design; in short:

- `ScheduledTrigger` gains one occurrence seam (`self._occurrence`) serving
  both an interval and a cron cadence — the bucket
  `floor(epoch(now)/interval)` generalizes to an occurrence identity, with
  `CronSchedule.occurrence_at_or_before(now)` as the cron implementation. The
  clock-gate/no-new-loop and at-most-once-per-occurrence dedup guarantees this
  spec describes for the interval path apply unchanged to cron.
- **UTC-only this increment.** Cron fields evaluate against `Clock.now()`'s UTC
  components directly; a per-trigger timezone (`tz`) is a noted, separate
  follow-up, not built now.
- **Fire-once-on-catchup.** If the harness was down over a scheduled
  occurrence, the first poll after restart fires it exactly once (the same
  `_seen`-seeding mechanism this spec's Dedup section already describes for
  interval mode, unchanged) — never zero, never more than one.
- `triggers/*.json` gains an optional `cron` field as a sibling of `interval`:
  exactly one of the two must be present. Every existing interval-only file
  keeps validating unchanged.
