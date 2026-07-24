# Processes — a top-level authoring aggregate over trigger + action + target (+ sink)

Status: draft
Date: 2026-07-22

## Goal

Give the operator a single, coherent object that ties together *how work is
born and where it goes*. Today the pieces are real but scattered: a
`triggers/*.json` file flatly mixes a cadence (`interval`), a condition
(`check` + `params`) and a destination (`target`), and the outbound side (how a
task's progress/outcome is reflected back to its origin) is welded inside a
`TaskSource` driver (`GithubTaskSource`). We introduce a **Process**: a named
aggregate that names, as distinct roles,

- a **trigger** — the cadence (the *when*),
- an **action** — the condition/producer (the *what*, a named `Check` + params),
- a **target** — the pipeline or queue the produced task enters (a workflow
  **or** a step; the dispatcher still does the placement),
- a **sink** — where the task's state is reflected back (the *outbound* side).

The operator defines several processes, each an independent `processes/*.json`
file. This is the "tie it all together" surface from the brainstorm.

The thesis, exactly as with generic triggers (ADR-0014): **we already have the
runtime mechanism, and a Process must not become a new runtime object.** A
Process is a **compile-time authoring aggregate** that decomposes into the
primitives that already exist — a `ScheduledTrigger` (a `TaskSource`) on the
inbound side, an existing workflow reference for the pipeline, and (in future)
a reflector on the outbound side. The orchestration core — `dispatcher`,
`consumer`, `router`, `source_poller` — never learns the word "process." That
single property is what keeps every invariant (#3/#8/#18–#20/#35) intact and
makes this change additive, not a rewrite.

## Scope of this increment

This spec deliberately builds the **same-origin, inbound-complete** slice and
leaves clear seams for the rest:

**In scope (built now):**

- The `Process` aggregate as data (`processes/*.json`) with the four roles above
  as *distinct, nested* fields (not the flat trigger shape).
- `FilesystemProcessRepository`: reads `processes/*.json`, validates fast, and
  **compiles each Process into a `ScheduledTrigger`** — reusing the entire
  generic-triggers machinery (`BUILTIN_CHECKS`, `ScheduledTrigger`, `dedup_key`,
  the clock-gate).
- The **`sink` slot in the schema**, parsed and validated, but restricted in v1
  to *absent* or `{"kind": "none"}` (fire-and-forget). This is the forward-compat
  commitment: the door is in the wall now, so adding a real sink later is a new
  driver + a flipped field, never a schema migration.
- Wiring (`cli.py` `_process_sources`, `harness init` writes `processes/`) and
  full `FakeClock` tests.

**Out of scope (seams left open, called out below so they aren't mistaken for
covered):**

- **A reflector registry / a real sink driver** (GitHub labels-as-sink, Slack).
  Only `none` ships. §"The sink seam" states the exact shape the future driver
  slots into.
- **The `github-issues` action** (scan issues by label as a `Check`). It needs a
  `GithubClient` injected into the check factory — a widening of the factory
  signature. §"The action seam" specifies where that injection goes so the
  factoring is additive when we do it.
- **Migrating `GithubTaskSource`** into an action + a reflector. Until a real
  sink exists there is nothing to migrate onto; `GithubTaskSource` stays whole.
- **A `ProcessAdmin` write-port + UI.** Processes are data on disk; the admin
  surface mirrors `AgentAdmin`/`WorkflowAdmin` and is a clean follow-up
  (invariant #33 shape), not part of this spec.

`triggers/*.json` keeps working unchanged — a Process compiles to the *same*
`ScheduledTrigger` a raw trigger file does, so both surfaces coexist. A Process
is the richer, primary authoring surface; a bare trigger file is the low-level
primitive it is built from.

## Terminology

An action's outputs are **Observations** — each becomes a **task** in the
inbox, placed by the dispatcher. The word **artifact** stays reserved for a
step's work products versioned in the worktree (`.artifacts/<id>/`,
ADR-0006). A Process therefore never "produces artifacts": it produces
observations that become tasks, whose steps may then produce artifacts.
Keeping the two words apart matters — both concepts flow through the same
documents.

## The Process shape

```json
{
  "name": "nightly-maintenance",
  "trigger": { "interval": "24h" },
  "action":  { "check": "always" },
  "target":  { "workflow": "maintenance" },
  "sink":    { "kind": "none" },
  "dedup":   "per-interval"
}
```

```json
{
  "name": "disk-pressure",
  "trigger": { "interval": "1h" },
  "action":  { "check": "disk-threshold", "params": { "path": "/", "percent": 80 } },
  "target":  { "step": "cleanup" },
  "dedup":   "per-state"
}
```

Field by field:

- **`name`** — the process identity. Defaults to the file stem. Becomes the
  trigger's `kind = "scheduled:<name>"` (unchanged from generic triggers).
- **`trigger.interval`** — a duration (`"1h"`, `"30m"`, `"24h"`), parsed by the
  existing `parse_interval`. This is the *only* key `trigger` carries in v1;
  nesting it (rather than a flat top-level `interval`) is what lets a future
  trigger type add its own keys under `trigger` without colliding with `action`.
- **`action.check`** + **`action.params`** — the inbound action: a check named
  against `BUILTIN_CHECKS`, built with `params`. Grouping the condition under
  `action` is the vocabulary alignment the brainstorm asked for — the "what
  happens on the trigger."
- **`target`** — exactly one of `{"workflow": name}` / `{"step": name}`. The
  dispatcher places the produced task (invariant #35); the Process never writes
  to a queue.
- **`sink`** — optional. Absent or `{"kind": "none"}` in v1. Where the task's
  progress/outcome is reflected back. See §"The sink seam."
- **`dedup`** — `"per-interval"` (default) or `"per-state"`, passed straight to
  `ScheduledTrigger`.

## Compilation: a Process is a `ScheduledTrigger`

`FilesystemProcessRepository.build()` reads each file and constructs a
`ScheduledTrigger` from its parts:

```
Process.trigger.interval  ->  ScheduledTrigger.interval  (parse_interval)
Process.action.check      ->  BUILTIN_CHECKS[check](params)  ->  ScheduledTrigger.check
Process.target            ->  ScheduledTrigger.workflow | step
Process.dedup             ->  ScheduledTrigger.dedup
Process.name              ->  ScheduledTrigger.name  ->  kind "scheduled:<name>"
Process.sink              ->  (v1: validated, then discarded — none has no driver)
```

The result is a `TaskSource` (a `Trigger`) that joins the existing `sources`
list. Every downstream piece — `SourcePoller` per source, `SourceReflectorSink`,
the source loop, `_seen` dedup — already handles it. **`build()` gains no
parameter; no new loop; no change to any orchestration module.** This is the
same "new source is a new driver behind an existing port" story ADR-0010 and
ADR-0014 already tell.

Validation fails fast at build (raising `ProcessValidationError` naming the
file), mirroring `FilesystemTriggerRepository`:

| Rejected | Reason |
|---|---|
| non-object JSON / broken JSON | not a definition |
| missing `trigger.interval`, malformed interval | no cadence |
| `action.check` not in the check registry | unknown action |
| `target` not exactly one of `{workflow}`/`{step}` | ambiguous/absent placement |
| `target` value not in `known_targets` (when supplied) | names no served workflow/step |
| `dedup` not in `{per-interval, per-state}` | unknown strategy |
| `sink.kind` not `"none"` (when `sink` present) | no sink driver in v1 |

## The sink seam (forward-compat, not built)

> **Update 2026-07-23 — partially realized.** The `slack` kind now ships:
> `compile_process` accepts `{"kind": "slack"}`, the compiled `ScheduledTrigger`
> stamps `data.sink = {"kind": "slack"}` into every task it fires (after the
> observation merge, so a check cannot clobber it), and
> `drivers/slack_sink.py`'s `SlackWebhookSink` — an outbound-only `TaskSource`
> registered in `cli._run` when `SLACK_WEBHOOK_URL` is set — routes on that
> destination identity, posting a stateless webhook message per report. Bullet
> 3's stateful create-then-update handle (and the `Reflector` port) remains
> open.

> **Update 2026-07-23 — routing unified.** Bullet 1's defaulting is no longer
> just a stated intention: `ports/source.py::effective_sink_kind(task)` is the
> one shared routing rule (`data.sink.kind` if present, else
> `data.source.kind`), and both `GithubLabelReflector` and `SlackWebhookSink`
> route their `_mine` through it. `github` is now an accepted `sink.kind`
> alongside `none`/`slack` — but it remains the *degenerate, same-as-origin*
> case, not an independent destination the way `slack` is: `GithubLabelReflector`
> can only ever target a task's origin issue (`data.source.issue`), so a
> Process-declared `{"sink": {"kind": "github"}}` is schema-valid and stamped
> onto every fired task, but stays inert unless the task's `data.source`
> already carries a matching GitHub repo/issue (as the `github-issues` check's
> observations do today). See ADR-0018 for the boundary this unification makes
> newly worth stating explicitly: a sink only reflects and can never fail or
> route a task, which is why `github`-as-sink (labels) stays a world apart
> from `open-pr` (PR creation, a finisher) even though both call GitHub.

The whole point of the `sink` field is to keep **source ≠ destination** open —
today a Process's origin and its reflection target are the same medium (or, in
v1, there is no reflection at all), but tomorrow a Process should ingest from
GitHub and reflect to Slack. Three shape-only commitments, made now, cost
nothing and keep that a *driver addition* rather than *surgery* (exactly the
posture agreed in the brainstorm):

1. **Route reflection by a destination identity, defaulted to the source.** The
   harness already routes outbound events by `task.data.source.kind`
   (`SourceReflectorSink`, invariant #19). When a real sink lands, a Process
   stamps a **`data.sink`** slot distinct from `data.source`, and the reflector
   sink routes on it, *defaulting to `source.kind` when `data.sink` is absent* —
   so same-origin processes need set nothing. In v1 no `data.sink` is written and
   no reflection occurs (a Process's `ScheduledTrigger` stamps no `data.source`
   either — it reflects nothing, exactly like every trigger today).

2. **The Process schema carries `sink` from day one.** It is validated but only
   `none` is accepted. A GitHub or Slack sink is a new allowed `kind` + a
   registered reflector driver — no schema change.

3. **A reflector may return persistable state.** The one genuine asymmetry
   between GitHub (reflect onto a pre-existing issue — stateless) and Slack
   (create a message, then edit it — needs the message id) is recorded here so
   the future `Reflector` port is shaped to let a reflector hand back a small
   opaque handle the harness persists onto the task and passes back on the next
   reflection. Invariant #21's "report twice is a no-op" becomes "convergent"
   (create once, update-or-skip thereafter) once outbound is stateful. **No
   `Reflector` port is built in this increment** — building a port whose only
   implementation is a no-op would be the over-engineering the brainstorm
   explicitly warned against. This bullet is the design note the future increment
   implements against.

## The action seam (forward-compat, not built)

The motivating example — "scan GitHub issues with a label and enqueue them" — is
a `Check` that lists issues and returns one `Observation` per issue (with
`state_key = "<repo>:<number>"` for `per-state` dedup and `data.source`
provenance for the reconcilers), swapping the pickup label as an idempotent
side effect (precedent: `GithubTaskSource.poll` / `ports/source.py`'s note that
`poll()` may side-effect idempotently). It is deferred here for one concrete
reason: a `Check` that talks to GitHub needs a `GithubClient`, but
`CheckFactory = Callable[[dict], Check]` takes only params.

The seam: `FilesystemProcessRepository.build()` will grow an optional
**dependency bag** (e.g. `github_client`, a slug resolver) passed to factories
that declare a need for it, exactly as `_github_sources` obtains a client from
`GITHUB_TOKEN` today. The clean `params`-only factory stays the default; a
source-bearing check is registered with a widened factory. This is additive —
no existing check changes — and is the immediate next increment.

## Model & UI: unchanged

- **`Task` does not change.** A Process compiles to a `ScheduledTrigger`, which
  sets `workflow_template`/`step` + `dedup_key` — all existing fields. No
  `data.source`, no `data.sink` in v1.
- **The board does not change.** A process-born task appears in `todo` and flows
  like any other. (A "created by process X" chip is an optional UI follow-up.)
- **`harness init`** writes an empty `processes/` next to `agents/`, `triggers/`
  and `repos.json`.

## Error states

| Situation | Detection | Where |
|---|---|---|
| malformed `interval` / unknown `action.check` / bad `target` / non-`none` `sink` | validated in `FilesystemProcessRepository` at build | fail fast at `harness run` start, like a missing agent spec |
| `check.evaluate()` raises | exception out of `poll()` | `SourcePoller.tick` catches it, emits `source_error`, retries next interval — identical to a trigger/GitHub `poll()` failure |
| target workflow unserved this run | `route()` / `ServedWorkflowRepository` at dispatch | task fails into `failed/` with a clear reason, exactly as a hand-submitted task naming an unserved workflow |

## Code structure (additions)

```
src/harness/
  drivers/
    fs_processes.py         # FilesystemProcessRepository (processes/*.json -> ScheduledTrigger)
  cli.py                    # _process_sources(); harness init writes processes/
docs/
  adr/0015-process-authoring-aggregate.md
```

No new port. No change to `dispatcher.py`, `consumer.py`, `router.py`,
`source_poller.py`, `models.py`, `ports/*`, `scheduled_trigger.py`, `checks.py`,
or `app.py`'s `build()` signature.

## Invariants — new (extend `CLAUDE.md`, they don't replace)

39. **A Process is a compile-time authoring aggregate, never a runtime object.**
    `processes/*.json` bundles a trigger (cadence), an action (a named `Check`),
    a target (workflow **or** step) and a `sink`, and `FilesystemProcessRepository`
    **compiles each into a `ScheduledTrigger`** that joins the existing `sources`
    list. Nothing under orchestration (`dispatcher`/`consumer`/`router`/
    `source_poller`) imports or names "process"; it is `cli.py` wiring turned into
    data. Guarded by `test_architecture.py` (`fs_processes.py` is a driver touched
    only by wiring). See ADR-0015.
40. **A Process's `sink` is a forward-compat seam: parsed, validated, `none`-only
    in v1.** The outbound reflection destination is a distinct field from the
    inbound origin so source ≠ destination stays open (GitHub in, Slack out) as a
    driver addition, never a schema change. v1 writes no `data.sink` and reflects
    nothing; a real sink routes via `SourceReflectorSink` on a destination
    identity that defaults to `source.kind`, and a stateful sink (create-then-
    update) may return a persistable handle — refining invariant #21 from
    "no-op" to "convergent." See ADR-0015.

## Completion check

The feature is done when:

1. A valid `processes/nightly.json` (`trigger.interval "1h"`, `action.check
   "always"`, `target {"workflow": "wf"}`) → `FilesystemProcessRepository.build()`
   yields one `ScheduledTrigger` with `kind == "scheduled:nightly"`, the interval
   parsed, the workflow wired; driven by `FakeClock` it fires once per bucket
   (same behaviour verified for generic triggers). No real sleeping.
2. A `disk-threshold`/`step` process with `params` and `dedup: "per-state"`
   builds a working trigger targeting that step.
3. `build()` fails fast (`ProcessValidationError` naming the file) on: broken
   JSON, missing/malformed `trigger.interval`, unknown `action.check`, a `target`
   that is neither/both, an out-of-`known_targets` target, an unknown `dedup`,
   and a `sink.kind` other than `"none"`.
4. `harness run` with an empty/absent `processes/` behaves exactly as today; with
   a `processes/*.json` present it wires the process as a `TaskSource` with no
   other change; `harness init` creates `processes/`.
5. Architecture tests still pass: `dispatcher`/`consumer` import neither
   `ports.source` nor `ports.triggers`; `fs_processes.py` is touched only by
   `cli.py`; `scheduled_trigger.py` still imports only ports/models/ids.

## Addendum (2026-07-23): cron cadence

A Process's `trigger` block now accepts `{"cron": "0 6 * * 1"}` as an
alternative to `{"interval": "1h"}` — exactly one of the two, same as generic
triggers (see the parallel addendum in `2026-07-22-generic-triggers-design.md`
and ADR-0018 for the full design). `compile_process`'s cadence parsing
generalizes from a single `_parse_interval` to `_parse_cadence`, returning
`(interval, cron)` with exactly one non-`None`; the error taxonomy in this
spec's "field" enum gains `cron` (a present-but-invalid cron expression) and
extends `trigger`'s existing meaning (an unsupported `trigger.kind`) to also
cover the block carrying both/neither cadence — one field, "the trigger block
itself is malformed," distinct from `interval`/`cron` meaning "this one value
is malformed." `ProcessFields` gains a `cadence` discriminator
(`"interval"`/`"cron"`) plus a `cron` value field; the admin form's Schedule
section gains a cadence toggle. UTC-only, fire-once-on-catchup — same as the
generic-triggers addendum. No change to the Process authoring aggregate's
other roles (action/target/sink) or to `Check`/`Observation`.
