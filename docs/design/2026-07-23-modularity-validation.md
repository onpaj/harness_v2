# Modularity validation — the composable-process vision vs. the code (2026-07-23)

## The vision under validation

The stated goal: **modularity first**. An operator composes a **Process** out of
six interchangeable slots, picking a concrete variant for each:

1. **Trigger** — *when* to act (a schedule, a filesystem condition, …).
2. **Action** — *what* to gather (load GitHub issues, load files, …); its outcome
   is a **list of items**.
3. **Queues + Workflow** — the items land on queues; a workflow (a state machine)
   drives which queue a task moves to next.
4. **Agent per queue** — every working queue is backed by an interchangeable agent.
5. **Finisher / resolver** — the finishing action (open a PR, create a file, call
   an API).
6. **Notifier** — progress notification during each step (a Slack message, a
   GitHub label change).

This document walks each slot, states whether the current code realizes it as an
*interchangeable* role (not merely as working code), and ends with a ranked punch
list of the gaps.

**Verdict up front:** the architecture matches the vision structurally. Five of
the six slots exist today as distinct, port-backed, independently swappable
roles; the invariants (ports-and-adapters, three-way decision split,
compile-to-primitives) are precisely the machinery that makes a slot swappable
without surgery. Two slots are incomplete — the **finisher** is the one place
the code contradicts its own modularity principle (a name-keyed hardwiring),
and the **notifier** is a reserved schema slot with no driver behind it yet.
Both are additive work, not redesign.

## Slot-by-slot assessment

### 1. Trigger — ✅ realized, with one cheap forward-compat item

`ScheduledTrigger` (`drivers/scheduled_trigger.py`) composes an **interval**
(data) × a **Check** (code) × a **target**. Invariants #35–#38 keep it honest: a
trigger produces tasks, never queue placements; cadence is a clock-gate;
dedup is bucket- or state-keyed and survives restarts through the same
`SourcePoller._seen` mechanism as GitHub ingestion.

Two observations against the vision:

- A "filesystem trigger" in the vision's sense is expressible **today** as a
  schedule × a filesystem *check* — the condition is the interchange point, and
  that's the `Check` port. Nothing is missing for that example.
- What does *not* exist is a non-schedule **cadence**: every trigger is
  poll-driven. An event-driven trigger (webhook, fs-watch) would be a new
  trigger kind. The Process schema nests `trigger` as an object already, but it
  carries only `interval` — there is no `kind` field reserved the way `sink`
  reserved one. Reserving `trigger.kind` (accepting only `"schedule"`) now is
  the same zero-cost move `_validate_sink` made in `drivers/fs_processes.py`,
  and keeps a future event trigger a driver addition rather than a schema
  migration.

### 2. Action — ✅ the seam exists and is proven; the catalog is thin

`Check.evaluate() -> list[Observation]` (`ports/triggers.py`) is exactly the
vision's "action returns a list of items". The registry (`BUILTIN_CHECKS` in
`drivers/checks.py`) maps a name to a factory; a Process names the check and
supplies `params`. The `github-issues` action (`drivers/github_issues_check.py`)
proves the hard case — a source-bearing action needing an injected
`GithubClient` — landed as a closed-over factory in `cli._process_sources`
without touching orchestration. "Load files from the filesystem" is a ~30-line
new `Check` plus one registry entry.

One asymmetry worth naming: **personas are data, actions are code.** Adding an
agent is a new JSON file (ADR-0007); adding an action is a new Python class.
That is a deliberate split ("the schedule is data, the condition is code"), and
`params` softens it, but it means an operator cannot introduce a genuinely new
action from the dashboard. A single generic data-driven check (e.g. "run this
command; each line of stdout is an observation") would close most of that
distance without giving up the typed built-ins.

### 3. Queues + workflow — ✅ the strongest part, one vocabulary clash

The mapping is one-to-one and in places *stronger* than the vision asked for:
observations become tasks in the inbox; **the dispatcher alone places them**
(invariant #35 — the producer never reaches past the dispatcher into a queue,
which the vision did not even demand); the workflow is an explicit state
machine with `END` reserved; a Process targets a workflow **or** a single step.

Vocabulary clash to guard in future specs: the vision calls the action's
outputs "artifacts". In harness vocabulary an **artifact** is a work product in
the worktree (`.artifacts/<id>/`, ADR-0006) — the action's outputs are
**Observations** that become **tasks**. Keeping the two words apart matters,
because both concepts flow through the same documents.

### 4. Agent per queue — ✅ fully realized

The persona is data (`AgentSpec`, invariant #14), the catalog maps a step name
to a spec (`AgentCatalog`, default identity name==step), a step's concurrency
is workflow config (`maxParallel`), and the admin UI edits agents through
`AgentAdmin`. Swapping the agent behind a queue is a file edit, no code. Nothing
to add.

### 5. Finisher / resolver — ⚠️ the biggest gap, and the one real principle violation

"Landing is a step, not magic" (invariant #12, ADR-0009) is the right *shape*:
the finishing action is an ordinary workflow node that can fail into `failed/`,
so **where** finishing happens is already composable. But **what** finishing
does is not:

- `app.py` hardwires the step *name* `"land"` to `LandingBehavior`
  (`LANDING_STEP = "land"`; `behavior_for()` branches on the step name).
- `LandingBehavior` does exactly one thing: push + `Forge.open_pull_request`.

So there is exactly one finisher in the system, selected by naming convention.
"Create a file" or "call an API" as a finishing action means a new behavior
class **plus a new name-keyed branch in `behavior_for`** — which is precisely
the shape invariant #14 forbids for personas ("no branch on the agent's name").
The finisher slot is the one place the codebase contradicts its own modularity
principle.

The fix mirrors ADR-0007: **finisher as data.** A finisher catalog (or a
`behaviors: {<step>: <finisher-kind>}` key in the workflow JSON) chooses a
step's finishing behavior by name from a registry, each kind backed by its own
port (`Forge` becomes the driver of the `open-pr` kind; `create-file` and
`call-api` are siblings). `behavior_for` then resolves through data, not a
hardcoded name, and the Process/workflow author picks the finisher the way they
already pick the agent.

### 6. Notifier — ⚠️ seam reserved, mechanism proven, no composable driver yet

Split this into what the vision actually asks for:

- **"Change a GitHub label" — done.** `SourceReflectorSink` fans
  `dispatched`/`finished`/`failed` events to every source; `GithubLabelReflector`
  is a standalone outbound-only notifier that keeps a task's issue labels live
  as it moves. Per-step notification exists and works today.
- **"A Slack message" / choosing the destination per Process — not built.** The
  Process schema carries `sink` from day one, but only `{"kind": "none"}` is
  accepted (invariant #40); no reflector registry, no `data.sink` stamping, no
  Slack driver. The spec (§"The sink seam") already fixes the future shape —
  routing through `SourceReflectorSink` on a destination identity defaulting to
  `source.kind`, and a stateful sink returning a persistable handle for the
  create-then-update (Slack) case.

So the notifier is a *deliberate, documented* gap with the schema slot already
in the wall. The remaining work is a registry keyed by `sink.kind` plus the
first real driver — additive by construction.

### 7. The Process glue — ✅ for what exists; it composes 4 of the 6 slots today

`processes/*.json` names trigger / action / target / sink as distinct nested
roles; `compile_process` is the single validator behind both startup and the
admin editor; the whole thing compiles to a `ScheduledTrigger` so orchestration
never learns the word "process" (invariant #39). The agent binding arrives
indirectly through the workflow — correct, that's the workflow's job. The
**finisher** is the one slot a Process (or even a workflow) cannot choose at
all today; it falls out of slot 5's fix.

## Why the architecture will bear the remaining weight

The two gaps close without touching a single invariant, and the invariants are
what guarantee that:

- **Compile-to-primitives** (invariant #39) means a new sink kind or trigger
  kind changes only `fs_processes.py` validation plus a driver — the runtime
  keeps seeing `TaskSource`/`EventSink` primitives it already knows.
- **The three-way decision split** (ADR-0002) and **ports-and-adapters**
  (ADR-0001) are why the `github-issues` action and the label reflector both
  landed without a line changed in `dispatcher.py`/`consumer.py`. The finisher
  fix follows the identical path ADR-0007 already walked for personas.
- The proof by history: every slot that *was* completed (action, agent,
  reflection) was completed as a driver addition. Nothing suggests the last two
  will differ.

## Risks to keep in view

- **Dedup semantics are at-most-once per interval, not suppress-while-in-flight**
  (documented in CLAUDE.md's gotchas). An operator composing a `per-interval`
  Process over a slow workflow will see an unresolved backlog re-fire each
  period. Deliberate, but it should be surfaced in the Process editor's help
  text, since Process authors are exactly the audience that will trip on it.
- **`BUILTIN_CHECKS` is code-level.** Fine for now; becomes a friction point the
  day operators (not developers) are the primary Process authors.
- **Terminology drift** ("artifact" vs. "observation") — cheap to police, costly
  to untangle later.

## Punch list (ranked)

1. **Finisher as data.** A finisher registry + workflow/Process-level selection;
   `Forge` becomes the `open-pr` driver; kill the name-keyed branch in
   `app.behavior_for`. Closes the only principle violation. (New ADR, mirrors
   ADR-0007.)
2. **First real sink.** Reflector registry keyed by `sink.kind`, stamp
   `data.sink`, route in `SourceReflectorSink` by destination identity; ship one
   driver (GitHub comment or Slack — Slack also exercises the stateful-handle
   shape the spec reserved). The spec's §"sink seam" is the blueprint.
3. **Reserve `trigger.kind`** in the Process schema (`"schedule"`-only for now)
   — the same zero-cost move the sink got, keeping event-driven triggers a
   driver addition.
4. **Filesystem action** (`fs-files` check) — cheap, no client dependency, and a
   second proof of the action seam.
5. **(Optional) one generic data-driven check** (command → observations) to let
   operators author simple actions without a code change.
6. **Vocabulary note in the specs**: action outputs are Observations/tasks;
   "artifact" stays reserved for `.artifacts/` work products.

## Addendum — the punch list has since landed (2026-07-23)

The body above was written when the finisher was still a name-keyed branch and
the sink was a reserved schema slot. Every punch-list item has since shipped, so
read the "⚠️" verdicts on slots 5 and 6 as *historical*:

1. **Finisher as data — done.** `Workflow.finishers` (step → kind) resolved
   against a registry in `app.build()`; `Forge` is the driver behind the
   `open-pr` kind; the name-keyed branch in `behavior_for` is gone. Conflicting
   or unknown bindings fail at build. See **ADR-0016** and **invariant #41**.
2. **First real sink — done.** A non-`none` sink is stamped as `data.sink` by
   the compiled trigger and reflected by `SlackWebhookSink` (`drivers/slack_sink.py`),
   routed on the *destination* identity through the existing `SourceReflectorSink`
   fan-out — GitHub-in / Slack-out made real. Stateless per-report post for now;
   the stateful create-then-update handle stays the documented future refinement.
   See **invariant #40**.
3. **`trigger.kind` reserved — done.** `_check_trigger_kind` in
   `drivers/fs_processes.py` accepts only `"schedule"`, the same zero-cost
   forward-compat move the sink got.
4. **Filesystem action — done.** `FileGlobCheck` ships as the `fs-files` check
   (`drivers/checks.py`), one observation per matching file — a second proof of
   the action seam, no client dependency.
5. **Generic data-driven check — done.** `CommandCheck` ships as the `command`
   check: one observation per non-empty stdout line, so an operator can author a
   simple action with no Python at all.
6. **Vocabulary policed.** CLAUDE.md's gotchas now carry the "'Artifact' is
   reserved vocabulary — an action's outputs are Observations" note.

One structural follow-on that the punch list did not name has also started: the
bespoke `GithubMergeabilityWatcher` is now the **`github-conflicts` process
action** (`GithubConflictsCheck`), and the watcher is **retired from the default
wiring** (`--watch-mergeability` defaults off) so conflict detection has one
authorable path. The class and its finisher-side companions
(`MergeReconciler`/`PrWatcher`) await a later deletion pass. The remaining open
items are all genuinely future work, exactly as the body predicted: an
event-driven trigger *kind*, the stateful Slack handle, and folding `triggers/`
into `processes/`.
