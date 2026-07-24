# ADR-0018: Healing as a Process

Status: Accepted

## Context

Self-healing (`docs/superpowers/specs/2026-07-21-self-healing-design.md`)
predated the Process idiom (ADR-0015). It was a bespoke core loop: `healer.py`
(`Healer`) was the single reader of `failed/` — it claimed a failed task, ran
the `healer` persona over a failure report (reason + history, no worktree),
opened a GitHub issue via `IssueTracker` (idempotent by the
`<!-- harness-heal:<id> -->` marker), and settled the task onto `healed/`. It
was enabled via `HealConfig` / `harness run --heal-repo <owner/repo>` and driven
by a dedicated `_heal_loop` in `Harness.run()`.

That was one orchestration idiom too many. ADR-0015 established the general
shape: a **Process** (`processes/*.json`) = trigger (cadence) × action (a named
`Check`) × target (workflow or step) × sink, compiled into a `ScheduledTrigger`
that rides the ordinary `sources` list. The Healer duplicated that shape with
private machinery — its own loop, its own config surface (`HealConfig`), its own
outbound path — and could never gain the `sink` slot (Slack visibility of heal
outcomes) the Process idiom gives every other automation for free.

## Decision

Express autohealing as an operator-authored Process and delete the bespoke
loop. `Healer`, `HealConfig`, and `Harness._heal_loop` are removed.

- **Action — a `failed-tasks` `Check`.** A new `FailedTasksCheck`
  (`drivers/failed_tasks_check.py`) claims each task out of `failed/` via the
  existing atomic `claim()`, settles the claimed original to `healed/` in the
  same `evaluate()` call, and emits one `Observation` per claimed task carrying
  the rendered failure report (reason + history) in `data`, with `state_key` =
  the failed task's id (`per-state` dedup). `failed/` still drains monotonically
  and exactly once. Claiming a pre-existing task source-side is the same class
  of idempotent, side-effecting claim `TaskSource.poll()` already sanctions
  (the GitHub label swap) — it is not a second router (invariant #3 holds).

- **Target — a two-step `heal` workflow.** `workflows/heal.json`:
  `heal` → `file-issue` → `end`. The target is `{"workflow": "heal"}`, **not**
  `{"step": "heal"}`: a workflow-less / single-step task is finished by the
  router after one hop, so a `file-issue` finisher step would silently never
  run and no issue would ever be filed. Any operator hand-authoring a
  multi-step process must point the target at the *workflow*. The `heal` step
  runs the unchanged `healer` persona (persona-as-data, invariants #14/9): it
  drafts the issue and returns a verdict, and opens nothing.

- **Deliverable — an `open-issue` finisher.** A new `open-issue` finisher kind
  (`behaviors/open_issue.py`, `OpenIssueBehavior`) sits alongside `open-pr` in
  the finisher registry (ADR-0016). The `file-issue` step is bound to it; it
  reads the drafted heal artifact and calls `IssueTracker.open_issue`, keeping
  the per-task idempotency marker. It fully *replaces* the step's behavior (like
  `open-pr`), so `file-issue` needs no catalog agent. Because the registry is
  now a registry of **factories** (`0018-finisher-registry-becomes-a-factory`),
  `open-issue` is registered as a factory that ignores `step`/`config`/`inner`.
  The finisher is deliberately generic — future observer/architect/QA processes
  reuse it.

- **Recursion guard by marker, not by construction.** The old loop guaranteed
  "no healing the healer" because it was the only reader of `failed/` and never
  wrote back. The check has no such structural guarantee, so it stamps a
  `data.heal` marker on the tasks it produces and skips a failed task carrying
  that marker, retiring it to `healed/` without a fresh `Observation`. A heal
  task that itself fails therefore passes through `failed/` normally
  (board-visible) once before the check's next tick retires it — a deliberate,
  recorded trade against the old "settle-note is the outcome" coupling.

- **`--heal-repo` survives as a thin generator, not a code path.** Rather than
  remove the flag, `harness run --heal-repo <owner/repo>` now (a) adds `heal`
  to the served set, (b) builds the `IssueTracker` with the unchanged
  token-presence logic (`MemoryIssueTracker` offline), (c) registers the
  `open-issue` finisher through `build()`'s pre-existing `finishers=` override,
  and (d) writes `processes/autoheal.json` via `FilesystemProcessAdmin`, but
  only if it does not already exist — a hand-edited process is never clobbered.
  `harness init` ships the `heal`/`file-issue` workflow and the `heal` agent
  unconditionally, but not `autoheal.json` (a bare init has no repo to file
  against).

- **Process compilation moves into `app.build()`.** The `failed-tasks` check
  must close over the harness's own live `events`/`failed`/`healed` queues,
  which only exist once `build()` has constructed them — a dependency the
  externally-cliented `github-issues`/`github-conflicts` checks never had. So
  `build()` gained `extra_checks` and `processes_root` (and lost `heal` and
  `issue_tracker`): it merges `cli.py`'s externally-dependent check factories
  over `BUILTIN_CHECKS` alongside its own internal `failed-tasks` factory, then
  compiles `processes/*.json` internally. The `SlackWebhookSink` *decision*
  still happens in `cli.py` before `build()` — a slack sink must be present in
  `sources` before `build()` constructs `SourceReflectorSink(sources)` — read
  cheaply off the raw declared sink kinds, no compilation (invariant #40). This
  is a narrow wiring relocation; the orchestration core still never imports or
  names "process".

## Consequences

- Healing is now an ordinary Process file with a `sink` slot: heal outcomes can
  be reflected to Slack like any other automation, for free.
- `failed/` and `healed/` are built unconditionally. With no `failed-tasks`
  process wired, `failed/` simply has no reader — exactly as before, but no
  longer gated on a `HealConfig` object.
- The single-reader-of-`failed/` guarantee is now the check's, and the
  deliverable is opened by a finisher, not a loop — invariants 24–27 are
  rewritten accordingly.
- A failed heal is briefly board-visible in `failed/` before the marker retires
  it, where the old loop swallowed it into a `healed/` note. This is the price
  of expressing healing in the general idiom, and is judged worth paying for
  the single-idiom simplification.
- Supersedes, by reference (not deletion, per ADR-0000's additive convention),
  the loop-specific parts of the self-healing design spec.
