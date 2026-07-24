# Plan: convert self-heal `Healer` into a Process

## Critical grounding note — read first

**This worktree's `main` is 72 commits behind `origin/main`, and every piece of
machinery this task assumes (`healer.py`, `IssueTracker`, `ports/triggers.py`
`Check`/`Observation`, `ScheduledTrigger`, `FilesystemProcessRepository`,
`processes/*.json`, the finisher registry, ADR-0015/ADR-0016, invariants
24–41) exists only on `origin/main`, not in this checkout.** `git log --all`
shows the same situation was hit and diagnosed by an earlier task on this repo
(commits `7c2b8ac`/`12f3eb0`): the fix was "merge-first as the plan's first
step," never a rebase (invariant #29: conflict resolution is always a merge).

Everything below is grounded by reading the real, current state of these files
**directly off `origin/main`** (via `git show origin/main:<path>`), not off
this stale worktree. **FR-0** — merging `origin/main` into this branch — is
therefore a hard prerequisite of the rough plan, not optional cleanup, and the
design/architecture/development steps must re-verify file line numbers and
signatures against the post-merge tree rather than trusting anything cited
here as an offset.

## Summary

Autohealing today is a bespoke core loop (`Healer`, sibling of `SourcePoller`,
wired via `HealConfig`/`--heal-repo`/`Harness._heal_loop`) that duplicates the
general Process idiom ADR-0015 already gave the harness. This task collapses
it into that idiom: a `failed-tasks` Check (the action), the existing `healer`
persona run as a `heal` step (the target), and a new generic `open-issue`
finisher kind (the outbound leg) — wired together by an operator-authored
`processes/autoheal.json`, with the Process's existing `sink` slot giving heal
outcomes Slack visibility for free. `healer.py`, `HealConfig`, `_heal_loop` and
`--heal-repo` as a bespoke path are removed.

## Context

`ports/triggers.py` (`Check`/`Observation`), `drivers/scheduled_trigger.py`
(`ScheduledTrigger`) and `drivers/fs_processes.py`
(`FilesystemProcessRepository`/`compile_process`) already express "trigger ×
action × target × sink" as data (ADR-0014/ADR-0015). Two check kinds already
follow the pattern this task extends to healing:

- `github-issues` (`drivers/github_issues_check.py`) — a `Check` needing a
  `GithubClient` + `RepositoryRegistry`, so it is **not** in the
  dependency-free `BUILTIN_CHECKS`; it's registered by a factory closed over
  those dependencies inside `cli.py::_process_sources`, which merges it into
  the `checks` dict handed to `FilesystemProcessRepository.build()`.
- `github-conflicts` (`drivers/github_conflicts_check.py`) — same shape, a
  second example of a dependency-bearing check registered the same way.

Separately, ADR-0016 already generalized a step's *finishing* behavior into
data: `Workflow.finishers: dict[str, str]` (step → finisher kind) resolved
against a registry `dict[str, ConsumerBehavior]` wired in `app.build()`
(default `{"open-pr": landing}`), with `behavior_for()` carrying no branch on
step name. Adding `"open-issue"` is exactly one more registry entry plus one
more `ConsumerBehavior` implementation — the seam this ADR was built for.

The `Healer` (`src/harness/healer.py` on `origin/main`) is the one piece of
automation that predates both ADRs and still hand-rolls all four roles: its
own `tick()` loop (trigger + claim), its own persona invocation (action), its
own `IssueTracker.open_issue` call inline in `_heal` (outbound), and no sink at
all. It is also the **only reader of `failed/`**, and the codebase leans on
that for a real invariant: nothing else may claim from `failed/`, and the
healer itself can never re-enter `failed/` (invariants 24/25 — "no healing the
healer" today holds *by construction*, since a `Healer.tick()` failure is
swallowed into a `heal-failed` note, never a requeue).

## Functional requirements

### FR-0 — Sync this worktree with `origin/main`

Merge `origin/main` (currently `1ef16f1`) into this branch before any other
step touches source. Must be a merge commit (invariant #29), not a rebase.
**Acceptance:** `git merge origin/main` completes (resolving any conflicts
that arise, none expected since this branch has no unmerged local commits
against `main`), `pytest -q` is green post-merge, and `healer.py`,
`ports/triggers.py`, `drivers/fs_processes.py`, ADR-0015/0016 are present on
disk.

### FR-1 — `failed-tasks` Check

A new driver module (e.g. `drivers/failed_tasks_check.py`) implementing
`Check`, registered as `"failed-tasks"` — **not** in the dependency-free
`BUILTIN_CHECKS`, but in `cli.py::_process_sources`'s locally-assembled
`checks` dict, mirroring exactly how `"github-issues"`/`"github-conflicts"`
are added there today (invariant #39 stays intact: `BUILTIN_CHECKS` itself
gains nothing).

Behavior of `evaluate()`:
- Claims one or more tasks currently sitting in `failed/`, using the queue's
  existing atomic `claim()` (the same lease/idempotency mechanism every other
  claimer uses — no new locking primitive).
- Per claimed task, immediately transfers it onto the terminal `healed/` queue
  (so `failed/` drains **monotonically and exactly once**, mirroring today's
  guarantee) and returns one `Observation` carrying:
  - `state_key` = the claimed task's id (so `per-state` dedup is a no-op
    safety net here — a task id is already unique per observation, but it's
    the mechanism `ScheduledTrigger` requires for `per-state` dedup, and it
    keeps a crash-and-retry from creating two heal tasks for one failure).
  - `data` = the failure report: reason (from the last history entry that
    carries one) + a rendered consumer-history summary, in the same shape
    `healer.heal_prompt`/`_consumer_history`/`_failure_reason` build today —
    reused, not reinvented, so the persona's prompt is unchanged in substance.
- **Recursion guard (invariant 25's replacement):** before claiming, skip (or
  rather: claim-and-settle-without-observing) a failed task whose `data`
  already carries a `heal` marker — i.e. a task that is itself a `heal` run
  that failed. Such a task still gets claimed and transferred to `healed/`
  (draining `failed/` stays exceptionless), but with a `heal-failed` note and
  **no** `Observation` — so it can never become a second heal task. The check
  stamps `data.heal = {"of": <original-failed-task-id>}` on every `heal`-target
  task it produces (via the Process's `target: {"step": "heal"}` /
  `ScheduledTrigger._task_for`'s `data` merge) so this marker is inspectable
  by any later step, not just by the check itself.

**Acceptance:**
- Claiming an empty `failed/` returns `[]` (no task fabricated) — parity with
  `AlwaysCheck`/other checks that no-op cleanly.
- Two failed tasks in `failed/` in one `evaluate()` call yield two
  `Observation`s and `failed/` ends empty, both settled to `healed/`.
- A failed task carrying `data.heal` is claimed, settled to `healed/` with a
  `heal-failed`-style note, and yields **no** `Observation`.
- A crash between claim and the caller's downstream processing cannot lose the
  task or duplicate it — it is only ever `.processing/`'d by the one queue,
  recovered by the existing `recover()` path exactly like any other queue.

### FR-2 — `heal` target (workflow or workflow-less step)

The `healer` persona (unchanged prompt content, still drafts `issue.md` +
returns a verdict, never opens anything itself — invariants 9/26 preserved)
runs as a step named `heal`, targeted by the Process's
`target: {"step": "heal"}` (a workflow-less single step is the natural fit,
since the healer has exactly one step today; a one-step `heal` workflow is
also structurally fine if the design step prefers uniformity with other
processes — decide there).

Must keep working **without requiring a real code worktree** — the persona
reasons over a failure report, not a checkout. Two candidate designs (decide
in the design step, record the reasoning in the design doc and the new ADR):

1. Give the `heal` step/process a `repository` (the harness's own repo) so
   `ClaudeCliBehavior`'s unconditional `self._workspace.attach(task)` keeps
   working unmodified — the persona incidentally gains code context, which may
   be useful for "is this a fixable harness bug" judgment.
2. Add repo-less support: either `Workspace.attach` tolerates
   `task.repository is None` (a scratch dir, not a git worktree), or
   `ClaudeCliBehavior` skips `attach()` when `task.repository is None`. This is
   a *port-level* change, so weigh it against the invariant that
   `RepositoryRegistry.resolve` is strict (`name: str`, no `None` case today)
   and that `ClaudeCliBehavior` currently has zero conditionals on `task`
   shape (keeping it that way is itself a soft invariant worth preserving if
   cheap).

**Acceptance:** whichever is chosen, a `heal` task drives the existing
`healer` persona to `issue.md` + a verdict exactly as today, and the decision
+ rationale is written down (design doc + ADR), not left implicit in code.

### FR-3 — `open-issue` finisher kind

A new `ConsumerBehavior` (e.g. `behaviors/open_issue.py` or folded into an
existing finishers module if the design step finds a better home) registered
in `app.build()`'s finisher registry as `"open-issue"`, alongside the existing
`"open-pr"` → `landing` default (ADR-0016's exact seam — a registry entry, not
a new branch in `behavior_for`).

On `run(task)`:
- Reads the `issue.md` artifact the `heal` step drafted (from
  `.artifacts/<id>/heal-NN/issue.md`, via `ArtifactView`/the worktree, per how
  the step actually persisted it under FR-2's decision).
- If the persona's verdict was "nothing actionable" (`request_changes` today —
  confirm the outcome vocabulary survives unchanged, since `behavior_for`
  still must not branch on step name, only the workflow/Process routes on
  outcome), the finisher step is simply never reached for that path — the
  workflow/Process's routing sends a `request_changes` outcome to a terminal
  other than the finisher, exactly as `heal_prompt`'s "no action" branch does
  today by not calling `IssueTracker` at all.
- On the `done` path, calls `IssueTracker.open_issue(repo, title=..., body=...,
  labels=..., marker=<the original failed task id>)` — reusing
  `GithubIssueTracker`/`MemoryIssueTracker` unmodified (out of scope per the
  task notes), and reusing the exact idempotency marker shape
  (`harness-heal:<id>`) so a crash between claim and finisher cannot file a
  second issue: a re-run resolves to the same marker and `IssueTracker`
  returns the existing issue.
- `marker` must be the **original failed task's id**, not the new `heal` task's
  id — carried through as `data.heal.of` from FR-1, since the `heal` task
  itself is a fresh id every time (idempotency has to survive the `heal` task
  being retried/re-attempted, not just the finisher call).

**Acceptance:**
- `build()` resolves `"open-issue"` with no branch on step name (mirrors the
  existing `"open-pr"` test coverage in `test_app.py`/wherever ADR-0016's
  registry is tested).
- An unknown finisher kind still fails at `build()`, not at consume time
  (existing behavior, must not regress).
- A `heal-failed` (agent error / `IssueError`) path settles the task without a
  second issue and without re-entering `failed/` a second time via this new
  path (mirrors `test_agent_error_settles_to_healed_and_does_not_loop` /
  `test_issue_error_settles_to_healed_and_does_not_loop` from today's
  `tests/test_healer.py`).
- Two independent runs against the same original failed-task id produce one
  GitHub issue (idempotency marker test, migrated from
  `test_done_verdict_files_an_issue_and_settles_to_healed`).

### FR-4 — Sink (Slack)

No new code path beyond what ADR-0015/§sink already ships: `processes/
autoheal.json` may declare `"sink": {"kind": "slack"}`, which
`ScheduledTrigger` stamps as `data.sink` on the emitted task, riding the
existing `SlackWebhookSink`/`_slack_sinks()` wiring in `cli.py`. **Acceptance:**
an autoheal process with `sink.kind = "slack"` and `SLACK_WEBHOOK_URL` set
posts a reflection when the heal task finishes/fails, using the exact
mechanism already covered by the sink's own tests — no new test category
needed here beyond "the autoheal process's compiled `ScheduledTrigger` stamps
`data.sink` like any other process," which existing `compile_process`/
`ScheduledTrigger` tests already generalize over.

### FR-5 — Remove the bespoke Healer path

Delete `healer.py`, `HealConfig`, `Harness.healer`/`Harness._heal_loop` and
the `_heal_loop` branch in `Harness.run()`'s `loops` list, and
`app.build()`'s `heal=`/`issue_tracker=` special-casing (the `healed_queue`
construction moves to being just another step's/process's queue, no longer
conditional on a `HealConfig`).

`--heal-repo`: **decide and document** (acceptance criterion, not left
ambiguous) which of:
(a) remove the flag entirely, updating `harness init`/README so the operator
    hand-authors or copies `processes/autoheal.json` and sets `GITHUB_TOKEN`
    themselves, or
(b) keep `--heal-repo <owner/repo>` as a thin CLI convenience that *writes*
    (or enables) a `processes/autoheal.json` with that repo baked into the
    `heal` step/target's `repository` (FR-2) — a generator, not a runtime
    branch.

Either way, `harness init` must ship (or the README must document how to
create) a `processes/autoheal.json` template, and the always-seeded
`agents/healer.json` persona-writing in `cli.py::_write_healer_agent` is
either kept as-is (the persona is unchanged) or folded into wherever `heal`'s
agent spec is now seeded (a workflow-less step's default agent, per
`_write_default_agents`'s existing per-step seeding path).

**Acceptance:** `grep -r HealConfig\|_heal_loop\|heal_repo` over `src/` and
`docs/` returns nothing except the chosen shim (if (b)) and historical/ADR
prose; `harness init` produces a working autoheal setup out of the box or a
one-line documented step to enable one.

### FR-6 — Invariants and ADR

Rewrite invariants 24–27 in `CLAUDE.md` (on `origin/main`) to describe the new
shape:
- **24** (today: "`failed/` has one reader — the healer") →
  "`failed/` has one reader — the `failed-tasks` Check" (still true; the
  reader changed identity, not cardinality).
- **25** (today: "the healer produces an issue, never a task, never writes
  back to `failed/`") → recursion guard now lives in the check
  (`data.heal` marker skip) plus the finisher never re-queuing; restate in
  terms of the Process pieces.
- **26** (today: "the healer's deliverable is opened by the worker loop, not
  the LLM") → "...opened by the `open-issue` finisher, not the LLM" — same
  claim (invariant 9), new home.
- **27** (today: "`IssueTracker` and the `Healer` loop are unknown to the
  dispatcher/consumer") → `IssueTracker` is now touched by the `open-issue`
  finisher (core-adjacent behavior, wired in `app.py`) and the `failed-tasks`
  check (drivers/, wired in `cli.py`) — restate the "who touches this port"
  list; the `test_architecture.py` guard shape (glob-based, mirrors
  invariants 32/34's `IssueChecker`/`MergeChecker` treatment) should get a
  same-shaped test if one doesn't already implicitly cover it via the generic
  "orchestration doesn't import X" checks.

New ADR (e.g. `docs/adr/0018-healing-as-a-process.md`) recording: why the
Healer predated the Process idiom, why collapsing it removes duplication
without losing the "no healing the healer" guarantee, and the FR-2 worktree
decision. It supersedes the relevant parts of the original self-healing design
doc (`docs/superpowers/...self-heal...` if present after the merge — locate
and cross-reference in the design step) without deleting it (ADRs are
additive/historical per `docs/adr/0000-adr-process.md`'s convention, confirm
in design).

### FR-7 — Migrate existing healer tests

`tests/test_healer.py`'s four scenarios (`done_verdict_files_an_issue`,
`request_changes_settles_without_an_issue`, `agent_error_settles_to_healed`,
`issue_error_settles_to_healed`) must have equivalent coverage through the new
path: `FailedTasksCheck` (or whatever it's named) unit-tested directly (claim
+ settle + Observation shape + recursion-guard skip), the `open-issue`
finisher unit-tested directly (idempotent marker, error → no loop), and at
least one end-to-end test driving a failed task through
`ScheduledTrigger`/`SourcePoller`/dispatcher/`heal` step/`open-issue` finisher
with `FakeClock` + in-memory drivers (`MemoryIssueTracker`,
`FakeAgentRunner`) to prove the whole chain composes — same spirit as
`tests/test_healer.py` today plus (if one exists after the merge) any
`test_healer_e2e.py`-style drive-until-quiet test.

## Non-functional requirements

- **Exactly-once drain.** `failed/` must still drain monotonically — no task
  claimed twice, none silently dropped, none re-entering `failed/` from this
  path (mirrors today's `Healer` guarantee word-for-word).
- **Idempotency.** Two heal attempts (crash-and-retry) for the same original
  failure must file at most one GitHub issue — preserved via the
  `harness-heal:<id>` marker, now keyed off `data.heal.of` rather than the
  claiming task's own id.
- **No new locking primitive.** The check uses `TaskQueue.claim()` exactly as
  every existing claimer does; no bespoke lease/lock is introduced.
- **Fail-fast configuration.** An `open-issue` binding to an unknown kind, or
  a malformed `processes/autoheal.json`, must fail at `build()`/process-compile
  time, never mid-run (matches ADR-0015/0016's existing posture).
- **No dispatcher/consumer branch.** Invariant #2/#3 hold: the check decides
  *whether* to observe, the dispatcher still decides *where* a `heal` task
  goes, the finisher registry — not `behavior_for`'s step-name branch —
  decides how it's finished.

## Data model

- **`Observation`** (existing type, reused) — `state_key` = original failed
  task id; `data` = `{"reason": str, "history": [...], "heal": {"of":
  <failed-task-id>}}` (shape to be finalized in design, but must carry enough
  for `heal_prompt`'s current fields: workflow, failing step, repository,
  reason, original request, consumer-history bullets).
- **`Task.data.heal`** — new convention, `{"of": <task-id>}`, stamped on every
  task the `failed-tasks` check produces; read by the check itself (recursion
  guard) and by the `open-issue` finisher (idempotency marker source).
- **`Task.data.sink`** — existing convention (ADR-0015 §40), unchanged, just
  now applicable to heal tasks via the Process's `sink` field.
- **`issue.md`** artifact — unchanged shape (`# title` + diagnosis + proposed
  change), written by the `healer` persona, now read by the `open-issue`
  finisher instead of by `Healer._heal`.
- **`processes/autoheal.json`** — new file, shape per FR-2/FR-4:
  ```json
  {
    "trigger": {"interval": "30s"},
    "action": {"check": "failed-tasks", "params": {}},
    "target": {"step": "heal"},
    "dedup": "per-state",
    "sink": {"kind": "none"}
  }
  ```
  (repository, if FR-2 chooses option 1, either as a top-level process field
  if `compile_process`/`ScheduledTrigger` already support a process-level
  `repository` — confirm against the post-merge `compile_process` signature —
  or via the `heal` step's own agent/workflow config.)

## Interfaces

- `Check.evaluate() -> list[Observation]` — the `failed-tasks` check's sole
  surface, unchanged port.
- `IssueTracker.open_issue(repo, *, title, body, labels, marker) -> IssueRef`
  — unchanged port, now called from the `open-issue` finisher instead of
  `Healer._heal`.
- `ConsumerBehavior.run(task) -> BehaviorResult` — the `open-issue` finisher's
  shape, same as `LandingBehavior`.
- `processes/autoheal.json` — the operator-facing authoring surface (FR-4
  data model above), readable/writable via the existing `ProcessAdmin`
  UI/port with no new admin surface needed (the `check_names()`/`sink_kinds()`
  enumeration already reflects new registry entries automatically once
  `"failed-tasks"` is added to the checks dict passed to
  `FilesystemProcessRepository`/`FilesystemProcessAdmin` — confirm both get
  the same `checks` dict in `cli.py`, or `ProcessAdmin`'s dropdown will omit
  it).

## Dependencies and scope

**Depends on:** the full Process/Check/finisher machinery already on
`origin/main` (ADR-0014/0015/0016 and everything they shipped) — hence FR-0.

**In scope:** everything in FR-0 through FR-7 above.

**Out of scope** (per the task notes, keep discipline here):
- Any change to `IssueTracker`/`GithubIssueTracker`/`MemoryIssueTracker`
  beyond reuse.
- The Observer/Architect/QA processes that will later reuse `open-issue` —
  this task only needs to prove the finisher is generic (not name-coupled to
  healing), not build the next consumer.
- Any change to `ScheduledTrigger`/`compile_process`/`ProcessAdmin` beyond
  what's strictly needed to register the new check kind and (if FR-2 needs it)
  a process-level `repository`/repo-less-step capability.
- Slack sink implementation itself (already shipped) — only its use is new.

## Rough plan

1. **FR-0**: merge `origin/main` into this branch; confirm `pytest -q` green
   and the referenced modules/ADRs exist on disk exactly as read above.
2. Design step: resolve the FR-2 worktree question (repo-bearing `heal` step
   vs. repo-less agent support) and the FR-5 `--heal-repo` question
   ((a) remove vs (b) thin shim); write both decisions + rationale into the
   design doc, feeding directly into the new ADR.
3. Implement `drivers/failed_tasks_check.py` (`FailedTasksCheck`): claim +
   settle-to-`healed/` + recursion-guard skip + `Observation` emission, unit
   tested against `MemoryTaskQueue` (mirroring `tests/test_healer.py`'s
   fixtures).
4. Wire `"failed-tasks"` into `cli.py::_process_sources`'s locally-assembled
   `checks` dict (needs the `failed`/`healed` `TaskQueue` instances — resolve
   the ordering question of *where* those instances get constructed relative
   to `_process_sources` vs. `build()`; this is the one piece of wiring with
   no existing precedent, since `github-issues`/`github-conflicts` close over
   external-service clients, not the harness's *own* queues — flag this
   explicitly to the design/architecture steps as the main integration risk).
5. Add the `heal` step's agent spec (persona unchanged) per FR-2's decision;
   seed it via `harness init` the same way other step personas are seeded.
6. Implement the `open-issue` finisher (`ConsumerBehavior`), register it in
   `app.build()`'s finisher registry, unit test idempotency + error path.
7. Author `processes/autoheal.json` (template shipped by `harness init` or
   documented), wire the sink per FR-4.
8. Remove `healer.py`, `HealConfig`, `_heal_loop`, and implement the FR-5
   `--heal-repo` decision.
9. Rewrite CLAUDE.md invariants 24–27 (FR-6) and write the new ADR.
10. Migrate `tests/test_healer.py` into the new shape (FR-7): direct unit
    tests for the check and the finisher, plus one end-to-end
    `FakeClock`/in-memory-driver test proving the full chain (matching
    whatever `test_healer_e2e.py`-equivalent exists post-merge, if any).
11. Full `pytest -q` + `test_architecture.py` guards green; update README's
    self-healing section to describe the Process-based setup.

## Open questions

- **Queue-wiring ordering (the main risk).** `github-issues`/`github-conflicts`
  checks close over *external* clients built independently of `build()`. The
  `failed-tasks` check needs the harness's *own* `failed`/`healed`
  `TaskQueue` instances, wired to the *same* `EventSink`/`BoardProjection`
  `build()` constructs internally, so the check's settle-to-`healed/` is
  board-visible. `_process_sources` runs before `build()` in `cli.py` today.
  Resolving this — a `build()` hook/callback, restructuring construction
  order, or handing `cli.py` its own pre-built queues that `build()` then
  reuses — is squarely a design-step decision; flagged here rather than
  guessed at.
- **Repo-bearing vs. repo-less `heal` step** (FR-2) — left to design, both
  options are viable and the choice affects whether `Workspace`/
  `RepositoryRegistry` need any port-level change.
- **`--heal-repo` fate** (FR-5) — remove vs. thin shim; either is acceptable
  per the task description, needs an explicit decision recorded, not silently
  dropped or silently kept.
- **Terminal queue name** — keep `healed/` as-is, or rename now that it's
  reached via a Process rather than a bespoke loop? The task notes flag this
  as open ("or a renamed terminal — see Notes"); default recommendation is
  **keep `healed/`** (no reason to churn a stable, documented terminal name
  purely for the refactor), but record the decision explicitly rather than
  defaulting silently.
- **Does a process-level `repository` field already exist on
  `ScheduledTrigger`/`compile_process`?** It appears to (`repository:
  str | None` param on both, sourced from `_process_sources`'s own `repository=
  None` today) — confirm in the design step whether it's *settable per
  process file* or only by the caller of `.build()`, since FR-2 option 1 needs
  it to be author-controllable per-process, not hardcoded.
