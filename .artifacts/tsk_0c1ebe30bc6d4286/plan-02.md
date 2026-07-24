# Plan (rev 2): convert self-heal `Healer` into a Process

## Why a rev 2 plan exists

This task already ran the full `plan → design → architecture` pipeline once
(`plan-01.md`, `design-01.md`, `architecture-01.md`, commits `66dd350`,
`c40bba0`, `829770e`). `development` then made its **first** move exactly as
`architecture-01.md §8.1` instructed — merging `origin/main` as a merge commit
(`9acd4e2`, invariant #29) — and then timed out after 1800s before writing any
further code. The operator restarted the task; restart re-inboxes it and the
dispatcher sent it back to `plan` (the workflow's start), not to
`development`, so the pipeline runs again from here.

**Nothing of substance needs to be redone.** Verified directly against this
worktree, not assumed:

- `HEAD` is `9acd4e2`, the merge commit itself — `git reflog` shows no
  commits after it, and `git status` is clean. Development did the merge and
  nothing else; there is no partial/lost implementation work to reconcile.
- FR-0's acceptance criterion is now met: `healer.py`, `ports/triggers.py`,
  `drivers/fs_processes.py`, ADR-0015/0016 are present on disk, and
  `.venv/bin/pytest -q` is green (**1221 passed, 1 skipped**) on this exact
  tree.
- `origin/main` has moved by exactly one commit since `architecture-01.md` was
  written (`e4485d6`, a docs-site change, already included in the merge) — so
  every file/line fact that document grounded against `origin/main` source
  still applies to this worktree as it stands right now. Spot-checked
  directly: `HealConfig`/`_heal_loop`/`heal=`/`healed_queue` still sit at
  `app.py:69,267,343,374,434,631,637-644,678`; `_process_sources`/
  `--heal-repo`/`_write_healer_agent` still sit at
  `cli.py:177,489,749,1563,1574-1587,1800-1802` — unchanged from the
  architecture doc's citations.
- `docs/adr/` still ends at `0017-landing-syncs-base-before-proposing.md` —
  `0018` is still the next free ADR number.

This document therefore **carries forward every decision `design-01.md` and
`architecture-01.md` already made and verified**, rather than re-deriving
them, and folds the architecture review's three resolved gaps (§3.1 sink
wiring order, §3.2 the invariant-#39 amendment, §3.3 the persona/artifact-path
fix) directly into the FRs below as settled requirements — not as open
questions for design to re-litigate. FR numbering is unchanged from
`plan-01.md` (FR-0 through FR-7) so the design/architecture docs' existing
cross-references (`§2.1`, `§4.2`, etc., keyed to these FR numbers) stay valid
if the next pass reuses them instead of writing from scratch. One functional
change from `plan-01.md`: **FR-0 is complete** and is recorded here as done,
not as a prerequisite.

## Summary

Autohealing today is a bespoke core loop (`Healer`, sibling of `SourcePoller`,
wired via `HealConfig`/`--heal-repo`/`Harness._heal_loop`) that duplicates the
general Process idiom ADR-0015 already gave the harness. This task collapses
it into that idiom: a `failed-tasks` Check (the action), the existing `healer`
persona run as a two-step `heal` → `file-issue` workflow (the target), and a
new generic `open-issue` finisher kind (the outbound leg) — wired together by
an operator-authored `processes/autoheal.json`, with the Process's existing
`sink` slot giving heal outcomes Slack visibility for free. `healer.py`,
`HealConfig`, `_heal_loop` and `--heal-repo` as a bespoke code path are
removed; `--heal-repo` survives only as a thin generator.

## Context

`ports/triggers.py` (`Check`/`Observation`), `drivers/scheduled_trigger.py`
(`ScheduledTrigger`) and `drivers/fs_processes.py`
(`FilesystemProcessRepository`/`compile_process`) already express "trigger ×
action × target × sink" as data (ADR-0014/0015). Two check kinds already
follow the pattern this task extends to healing — `github-issues`
(`drivers/github_issues_check.py`) and `github-conflicts`
(`drivers/github_conflicts_check.py`): both need dependencies (a
`GithubClient`, a `RepositoryRegistry`) `BUILTIN_CHECKS` can't carry, so both
are registered by a factory assembled in `cli.py::_process_sources` and
merged into the `checks` dict handed to `FilesystemProcessRepository`.
Separately, ADR-0016 already generalized a step's *finishing* behavior into
data: `Workflow.finishers: dict[str, str]` resolved against a registry wired
in `app.build()` (default `{"open-pr": landing}`), `behavior_for()` carrying
no branch on step name.

`healer.py`'s `Healer` predates both ADRs and hand-rolls all four Process
roles itself: its own `tick()` loop (trigger + claim), its own persona
invocation (action), its own inline `IssueTracker.open_issue` call in `_heal`
(outbound), no sink. It is the **only** reader of `failed/`, and two real
invariants lean on that: nothing else may claim from `failed/` (#24), and the
healer can never re-enter `failed/` — "no healing the healer" holds *by
construction* today because `Healer.tick()` swallows its own failures into a
`heal-failed` note rather than a requeue (#25).

The `failed-tasks` check the design settled on replaces "guaranteed by
construction" with "guaranteed by a recursion-guard marker" (`data.heal`),
because moving the heal attempt through the ordinary `heal`/`file-issue` steps
means a heal-attempt failure now travels through `Consumer._fail` like any
other task failure — which is a **deliberate, positive** side effect: it makes
a stuck healer board-visible in `failed/` for the first time, rather than
silently retried forever inside one opaque loop.

## Functional requirements

### FR-0 — Sync this worktree with `origin/main` — **DONE**

Merged at `9acd4e2` (`git merge origin/main`, a merge commit per invariant
#29, not a rebase). Verified on this tree, not assumed: `healer.py`,
`ports/triggers.py`, `drivers/fs_processes.py`, ADR-0015/0016 exist on disk,
and `.venv/bin/pytest -q` passes (1221 passed, 1 skipped). No further action
needed; nothing after this commit exists to reconcile.

### FR-1 — `failed-tasks` Check

A new driver module `drivers/failed_tasks_check.py` implementing `Check`,
registered as `"failed-tasks"` — **not** in the dependency-free
`BUILTIN_CHECKS` (`drivers/checks.py`), but merged into a `checks` dict
assembled **inside `app.build()`** (this is the one place this task departs
from the `github-issues`/`github-conflicts` precedent of registering in
`cli.py`: the check needs the harness's *own* `failed`/`healed`/`events`
instances that only exist once `build()` has constructed them, not an
externally-owned client `cli.py` can hand it independently — see
`design-01.md §3.4` / `architecture-01.md §3.1,§4.5`, both affirmed).

Contract:

```python
class FailedTasksCheck(Check):
    def __init__(self, *, failed: TaskQueue, healed: TaskQueue,
                 events: EventSink, clock: Clock) -> None: ...
    def evaluate(self) -> list[Observation]: ...
```

`evaluate()`, per call: snapshot `failed.list()`, `claim()` each (a lost race
is a no-op, matching `test_lost_claim_race_is_a_noop`'s existing pattern —
never an error). An empty `failed/` returns `[]` with **no claim attempted**.
Per successfully claimed task:

1. **Recursion guard first.** If `task.data.get("heal")` is present (this
   claimed task is itself a `heal`-workflow task that failed), settle it
   straight to `healed/` with a `"heal-failed: the heal attempt itself
   failed"` note and produce **no** `Observation`.
2. **Otherwise**, settle to `healed/` with a `"queued for healing"` note and
   return one `Observation`:
   - `state_key = task.id` (the original failed task's id — `per-state`
     dedup; belt-and-suspenders against a crash-and-retry producing two heal
     tasks for one failure, since the id is already unique).
   - `data` carries **both** the structured failure-report fields
     (`reason: str`, `history: list[str]`, independently unit-testable) *and*
     a **rendered markdown** version in `data["body"]` — reusing
     `_failure_reason`/`_consumer_history` moved verbatim out of `healer.py`
     into the new check module (or a small shared `heal_report.py`; either is
     architecture-clean per invariant 17, neither imports beyond `models`).
     The rendered form is not optional decoration: `heal` runs through the
     **generic** `ClaudeCliBehavior`/`compose_prompt`, which only ever reads
     `task.data["request"]`/`task.data["body"]` — with no rendered `body` the
     persona receives no failure report at all (architecture-01 §3.3a; this
     is the single most important correctness fix carried forward from that
     review — it is easy to silently regress by stopping at the structured
     fields alone).
   - `data["request"]` = a synthesized one-line diagnostic prompt (e.g.
     `f"Diagnose why task {original.id} failed at step {original.status!r} "
     f"(workflow {original.workflow_template!r})."`) — new content, not the
     original task's own request, documented as such in the check's
     docstring.
   - `data["original_request"]` = `_request_of(original)` carried through
     separately (distinct from the synthesized `data["request"]` above) so
     `OpenIssueBehavior`'s title fallback chain matches `_title`'s existing
     chain exactly.
   - `data["source"]` = `original.data.get("source")` when present (the
     `_body()` "Origin: <url>" footer depends on this; without it the footer
     silently vanishes because `OpenIssueBehavior` only ever sees the fresh
     heal task, not the original).
   - `data["heal"] = {"of": task.id}` — the recursion-guard marker, also the
     idempotency-marker source for FR-3.

**Acceptance:**
- Claiming an empty `failed/` returns `[]`, no claim attempted.
- Two failed tasks claimed in one `evaluate()` call yield two `Observation`s,
  both settled to `healed/`, `failed/` ends empty.
- A failed task carrying `data.heal` is claimed, settled to `healed/` with the
  `heal-failed` note, yields **no** `Observation`.
- `data["body"]` on a yielded `Observation` actually contains the rendered
  failure-report markdown (title/reason/history bullets) — not just that
  `data["reason"]`/`data["history"]` exist as structured fields (this is new
  coverage `plan-01.md` didn't call for; added per the architecture review).
- A crash between claim and the caller's downstream processing loses nothing
  and duplicates nothing — recovered by the existing `recover()` path like any
  other queue, no bespoke lease.

### FR-2 — `heal` → `file-issue`: a two-step workflow, repo-less

**Decided** (by `design-01.md §3.2` and reaffirmed by `architecture-01.md
§2.1,§4.3`; not reopened here): `heal` is **not** workflow-less. A step has
exactly one bound behavior, and the persona (drafts + verdict) and the
deliverable (opens the issue) need distinct step names joined by a routing
edge, the same separation `plan → design → ... → land` already models:

```jsonc
// workflows/heal.json
{
  "start": "heal",
  "transitions": [
    {"from": "heal", "on": "done", "to": "file-issue"},
    {"from": "heal", "on": "request_changes", "to": "end"},
    {"from": "file-issue", "on": "done", "to": "end"}
  ],
  "finishers": {"file-issue": "open-issue"}
}
```

`heal` is an ordinary catalog-driven step (`ClaudeCliBehavior`, unchanged
class). It must run **without a real code checkout** — the persona reasons
over a failure report, not a diff. **Decided:** `Workspace.attach` grows a
branch that tolerates `task.repository is None`
(`GitWorkspace.attach` — insert the check *before* the existing
`self._registry.resolve(task.repository)` call, never call `resolve(None)`),
git-initializing a standalone scratch repo at
`<worktrees_root>/<task_id>` with the same reset-on-reattach semantics the
existing override-reattach path already has, applied to its own root commit
instead of a shared repo's HEAD. `push()` is genuinely never called for a
repo-less task (the `heal`/`file-issue` workflow never reaches `land`), so no
new branch is needed there — document as an implicit contract in
`GitWorkspace`'s module docstring: *a repo-less task's workflow must end
before any step that pushes.* `RepositoryRegistry` and `ClaudeCliBehavior`
need **zero** changes (`attach()` is called unconditionally with no
inspection of `task.repository` today, and stays that way). This new git code
is on a robustness-sensitive path — give `_attach_repo_less` the same
idempotent-re-entry care as the existing reattach logic; never assume the
directory is empty.

**Persona/prompt fix — required, not optional (architecture-01 §3.3, folded
in here as a plan-level requirement):**
- Edit the `_HEALER_PERSONA` text's one file-writing sentence: replace *"write
  a proposed GitHub issue to the file `issue.md` in your working directory"*
  with wording that defers to `compose_prompt`'s generic artifact-path line
  (e.g. *"write a proposed GitHub issue to the file the harness told you to
  write your output to above"*) — bringing `heal` in line with every other
  persona in `AGENT_PERSONAS`, none of which hardcode a filename. This is the
  **only** edit to the persona; its judgment/verdict logic is unchanged
  (invariants 9/26's real intent — "still only drafts + verdicts, never opens
  anything" — is preserved; record this narrow, deliberate edit in the ADR so
  it doesn't read as scope creep).
- The artifact therefore lands at the **generic, flat, attempt-indexed** path
  every other step uses (`.artifacts/<task_id>/heal-NN.md`, per
  `artifacts_layout.py`'s `next_attempt`/`STEP_ATTEMPT` convention) — **not**
  a per-attempt directory with a fixed filename, and **not** the worktree
  root. This is a genuine improvement over today, not just a fix: the heal
  deliverable becomes visible in the standard artifacts UI for the first
  time, where today's scratch-dir `issue.md` never was.

**Acceptance:** a `heal` task drives the existing `healer` persona to a
generic-path artifact + a verdict, with no worktree/checkout required; the
one-sentence persona edit and the repo-less-attach mechanism are both
documented in the ADR.

### FR-3 — `open-issue` finisher kind

A new `ConsumerBehavior` in `behaviors/open_issue.py`, registered in
`app.build()`'s finisher registry as `"open-issue"` alongside the existing
`"open-pr"` → `landing` default — a registry entry, not a new branch in
`behavior_for()`.

```python
class OpenIssueBehavior(ConsumerBehavior):
    def __init__(self, *, tracker: IssueTracker, repo: str,
                 artifacts: ArtifactView, clock: Clock,
                 labels: tuple[str, ...] = ("harness:self-heal",)) -> None: ...
    async def run(self, task: Task) -> BehaviorResult: ...
```

`run()`:
1. Locate the `heal` step's artifact via `artifacts.list(task.id)` filtered to
   `ref.step == "heal"`, highest `attempt` (mirrors the general
   latest-attempt convention; in practice a single candidate per fresh heal
   task).
2. Build `title`/`body` — the exact `_title`/`_body` logic moved verbatim out
   of `healer.py`, reading `task.data["original_request"]` (not a fresh
   `_request_of(task)`, which would surface the synthesized diagnostic
   sentence instead) and `task.data.get("source")` (both carried through by
   FR-1, so the "Origin: <url>" footer survives).
3. `marker = task.data["heal"]["of"]` — the **original** failed task's id
   (confirmed unconditionally carried through `ScheduledTrigger._task_for`'s
   `data = {**obs.data}` merge — nothing strips `heal` before it), never the
   fresh `heal` task's own id, so idempotency survives the `heal` task itself
   being retried.
4. `self._tracker.open_issue(self._repo, title=title, body=body,
   labels=self._labels, marker=marker)` — **no try/except**: `Consumer.tick()`
   already wraps `behavior.run()` in a blanket `except Exception` → `_fail`,
   so an `IssueError` here lands the task in `failed/` exactly like an agent
   exception does, and FR-1's recursion guard is what stops that from
   looping — not in-behavior error handling (confirmed against
   `consumer.py`, this is the single guard covering both failure sites,
   simpler than today's bespoke `try/except` in `Healer._heal`).
5. `return BehaviorResult(Outcome.DONE, f"opened issue {ref.url}")`.

The `"nothing actionable"` (`request_changes`) verdict path never reaches
`file-issue` at all — the workflow's own routing sends `request_changes`
straight to `end` (FR-2's JSON above), so `behavior_for` still never branches
on the verdict; no dead code inside the finisher for that case.

**Acceptance:**
- `build()` resolves `"open-issue"` with no branch on step name (same
  coverage shape as the existing `"open-pr"`/`test_app.py` test).
- An unknown finisher kind still fails at `build()`, never mid-run.
- A `heal-failed` path (agent error / `IssueError`) settles without a second
  issue and without a special-cased re-entry into `failed/` — it's the same
  ordinary `Consumer._fail` path every other step uses, retired next tick by
  FR-1's recursion guard (mirrors
  `test_agent_error_settles_to_healed_and_does_not_loop`/
  `test_issue_error_settles_to_healed_and_does_not_loop` from today's
  `tests/test_healer.py`).
- Two independent runs against the same original failed-task id produce one
  GitHub issue (idempotency marker test, migrated from
  `test_done_verdict_files_an_issue_and_settles_to_healed`).
- An issue filed for a task whose original carried `data.source` includes an
  "Origin: ..." line (new coverage — the source carry-through only matters
  once the task is re-created rather than operated on directly, so today's
  `tests/test_healer.py` has no equivalent case).

### FR-4 — Sink (Slack), including the wiring-order fix

No new sink code — `processes/autoheal.json` may declare `"sink": {"kind":
"slack"}`, riding the existing `SlackWebhookSink`/`data.sink` stamping
(ADR-0015 §40) unchanged. The one real risk here, resolved by
`architecture-01.md §3.1` and **required**, not optional, in this
implementation: moving process compilation inside `app.build()` (FR-1's
wiring decision) happens *after* `build()` already constructs
`SourceReflectorSink(sources)`, so a `SlackWebhookSink` decided *from* the
compiled process list would never reach that fan-out (it's a plain list
reference closed over at `__init__`, never re-read).

**Fix, carried forward as a requirement:** decouple the Slack-sink *decision*
from process *compilation*. Add `cli.py::_declared_sink_kinds(processes_root:
Path) -> set[str]` — a standalone helper that globs `processes/*.json`,
`json.loads`s each, and collects `raw.get("sink", {}).get("kind")` with no
`Check`/`compile_process` involved (a malformed file's failure surfaces
later, loudly, when `build()` actually compiles it — this pre-scan only
decides "should a `SlackWebhookSink` exist"). Call it in `cli.py` **before**
`build()`, feed its result into the existing "build `SlackWebhookSink` when
`SLACK_WEBHOOK_URL` is set, else warn" logic (unchanged otherwise), and pass
the resulting sink through the `sources=` list `build()` already accepts —
present at `SourceReflectorSink` construction time, exactly as today.
Process compilation's own output (`process_sources`, including the compiled
`autoheal` trigger) then merges **only into `build()`'s internal `pollers`
list**, never backfilled into `SourceReflectorSink` — safe, because every
`Trigger` subclass (including `ScheduledTrigger`) inherits
`report_progress`/`finish` as no-ops (invariant #36); `SlackWebhookSink` is
the one non-`Trigger` `TaskSource` in this picture and step above is what
gets it into the fan-out correctly.

**Acceptance:** an autoheal process declaring `sink.kind = "slack"` with
`SLACK_WEBHOOK_URL` set actually posts a reflection when the heal task
finishes/fails — this is the concrete, previously-silent failure mode the fix
closes; without it FR-4's acceptance would pass by coincidence (the chain
completes) while never posting.

### FR-5 — Remove the bespoke Healer path; `--heal-repo` as a thin generator

Delete `healer.py`, the `HealConfig` dataclass, `Harness.healer`,
`Harness._heal_loop`, the conditional `_heal_loop` entry in `run()`'s `loops`
list, the `if self.healer is not None` guard in `recover()`
(`healed`/`failed` both become **unconditionally** recovered/constructed —
recovering an idle queue is free, matching the existing rationale for why
`done/` is unconditionally recovered today), and `app.build()`'s `heal=`
parameter and its conditional `healed_queue`/`include_healed` construction.
`healed_queue` construction moves up to sit unconditionally alongside
`failed`/`done`/`archived`, before the new `checks` dict (FR-1) needs it.

**Decided** (`design-01.md §3.5`, reaffirmed `architecture-01.md §2.2`): keep
`--heal-repo <owner/repo>` as a **thin generator**, not remove it. On
`--heal-repo`: add `"heal"` to `served_names`, build `issue_tracker` with the
unchanged token-presence logic, construct `OpenIssueBehavior` directly and
hand it in via `build()`'s pre-existing `finishers={"open-issue": ...}`
override (already tested by
`test_caller_supplied_finisher_registry_entry_is_used` — confirm this test
survives, extend rather than duplicate its shape), pass
`extra_checks=_process_check_factories(...)` (see FR-1's `app.build()`
signature below), and write `processes/autoheal.json` via
`FilesystemProcessAdmin` **only if it doesn't already exist** (never
clobbering an operator's hand-edited file — validated by the same
`compile_process` the admin already runs, so a malformed write is impossible
by construction).

**Acceptance:** `grep -rE "HealConfig|_heal_loop" src/ docs/` returns nothing
except historical/ADR prose; `grep -r heal_repo src/` returns only the thin
generator's own code; `harness init --heal-repo <owner/repo>` (or the
run-time flag, per the actual CLI shape) produces a working autoheal setup
with no further manual steps.

### FR-6 — `app.build()`/`cli.py` wiring shape (new — makes explicit what
FR-1/FR-4/FR-5 require of the two wiring modules, previously scattered across
FR-1/FR-4/FR-5's prose in `plan-01.md`)

`app.build()` gains exactly two new parameters (and no others — `issue_repo`/
`issue_tracker` wiring for `OpenIssueBehavior` stays entirely in `cli.py`'s
`finishers=` call, `build()` doesn't need to know about `IssueTracker`):

- `extra_checks: dict[str, CheckFactory] | None = None` — merged over
  `BUILTIN_CHECKS`; `github-issues`/`github-conflicts`'s existing
  `cli.py`-side factories are unchanged in content, just still assembled in
  `cli.py` and passed through this parameter (renamed there from
  `_process_sources` to `_process_check_factories`, matching its narrowed
  job once it stops calling `FilesystemProcessRepository` itself).
- `processes_root: Path | None = None` — defaults to `layout.processes`.

Inside `build()`, right before the existing `pollers = [...]` construction,
after `events`/`failed`/`healed_queue`/`known_steps` all exist:

```python
checks = {
    **BUILTIN_CHECKS,
    **(extra_checks or {}),
    "failed-tasks": lambda params: FailedTasksCheck(
        failed=failed, healed=healed_queue, events=events, clock=clock
    ),
}
process_sources = FilesystemProcessRepository(processes_root or layout.processes).build(
    clock=clock, checks=checks, repository=None,
    worktree_root=str(layout.worktrees), known_targets=set(known_steps),
)
all_sources = [*sources, *process_sources]   # feeds pollers ONLY — see FR-4
pollers = [SourcePoller(source=s, inbox=inbox, events=events) for s in all_sources]
```

`cli.py`'s `known_targets` computation must include `"heal"` — `served_names`
(including the `--heal-repo`-driven append) must be finalized **before** that
block runs, so a bare trigger or the autoheal process itself validates
against a `known_targets` set that actually contains `heal`.

`harness init` seeds `agents/heal.json` through the **generic**
`_write_default_agents` path (add `"heal"` to `AGENT_PERSONAS`/`AGENT_MODELS`,
persona per FR-2's one-sentence edit, model tier `opus` unchanged) and always
writes `workflows/heal.json` (dormant data, mirroring `workflows/resolver.json`
today) — but **not** `processes/autoheal.json` itself, which stays gated
behind `--heal-repo` (a bare `harness init` has no repo to file issues
against). `_write_default_agents`'s `if step == LANDING_STEP: continue` skip
generalizes to `if workflow.finisher_for(step) is not None: continue` (also
covers `file-issue`, which needs no agent spec) — strictly less code than a
second hardcoded carve-out, do this generalization rather than adding one.

**Acceptance:** invariant #39's "`build()` gains no parameter" claim is
amended (not silently contradicted — see FR-6 in the invariant rewrite below)
to record exactly why these two parameters exist; `test_architecture.py`'s
existing driver-import guards need no new test (they already generalize) —
confirm this holds once the two glob-based `healer`-specific tests are
deleted (next FR).

### FR-7 — Invariants and ADR

Rewrite `CLAUDE.md` invariants **24–27**, and **append** (don't replace) to
**35** and **39**:

- **24.** `failed/` has one reader — the `failed-tasks` Check (an action of an
  operator-authored Process, typically `processes/autoheal.json`); `healed/`
  is the never-consumed terminal. Both queues are now **unconditionally**
  built — with no `failed-tasks`-driving process configured, `failed/` simply
  has no reader.
- **25.** The check produces at most one fresh task per claimed failure and
  never writes a claimed task back to `failed/` — every claim settles to
  `healed/` in the same `evaluate()` call. Recursion is guarded by a marker
  (`data.heal`), not by construction: a heal task that itself fails **does**
  pass through `failed/` normally (board-visible) before the check's next
  tick retires it without a new `Observation`.
- **26.** The heal deliverable is opened by the `open-issue` finisher (a
  `ConsumerBehavior`, same footing as `open-pr`/`LandingBehavior`), not the
  LLM — invariant 9 unchanged, new home.
- **27.** `IssueTracker` is touched by the `open-issue` finisher (wired via
  `build()`'s `finishers=`) and `FailedTasksCheck` is touched only as a
  `Check` registered inside `app.build()`'s internal checks dict — neither is
  known to the dispatcher or consumer. The two healer-specific
  `test_architecture.py` tests (`test_healer_imports_only_ports_models_and_ids`,
  `test_orchestration_does_not_import_issues_or_healer`) are **deleted**, not
  adapted (their subject no longer exists); keep the still-relevant half of
  the second as a standalone "`dispatcher.py`/`consumer.py` never import
  `ports.issues`" check, mirroring invariants 32/34's shape.
- **35 (append).** `FailedTasksCheck`'s claim-and-settle of a *pre-existing*
  task is the same class of "idempotent, side-effecting claim action"
  `TaskSource.poll()`'s docstring already sanctions, distinct from "a trigger
  places the *new* task it produces" — still the dispatcher's alone.
- **39 (append).** *`build()` gained two parameters (`extra_checks`,
  `processes_root`) when the `failed-tasks` check needed to close over ports
  `build()` itself constructs (the live `events`/`failed`/`healed` — see
  ADR-0018) — a class of dependency `github-issues`/`github-conflicts`
  (external clients, wired entirely in `cli.py`) never had. Process
  compilation itself is still a `cli.py`/`app.py` wiring-time concern; the
  orchestration core still never imports or names "process" — that half of
  this invariant is unchanged.*

Also sweep `models.py`'s `FAILED`/`HEALED` docstrings (currently say *"drained
by the `Healer` loop"* by name) — stale source comments, not `CLAUDE.md`, but
wrong the moment `healer.py` is deleted; fix in the same change.

New ADR `docs/adr/0018-healing-as-a-process.md` (confirmed: `0018` is the
next free number). Records: why `Healer` predated the Process idiom and
duplicated it; the repo-less-`heal`-step decision and rejected
repo-bearing alternative; the `--heal-repo`-thin-shim decision and rejected
"remove entirely" alternative; the process-compilation-inside-`build()`
relocation and its narrow scope relative to ADR-0015; the deliberate
one-sentence persona wording edit and why it doesn't count as "the persona
changed" in the invariant-9/26 sense; and the settle-note/heal-outcome
decoupling ("queued for healing" is not the eventual outcome — a deliberate,
recorded trade, not an oversight). Supersedes the relevant parts of
`docs/superpowers/specs/2026-07-21-self-healing-design.md` by reference
(confirmed present on disk at that path post-merge), without deleting it, per
`docs/adr/0000-adr-process.md`'s additive convention.

### FR-8 — Migrate existing healer tests (renumbered from `plan-01.md`'s FR-7,
since this document adds FR-6 as a new wiring-shape FR; content unchanged
from the design's test-migration table, plus the two additions architecture
flagged)

`tests/test_healer.py`'s four scenarios must have equivalent coverage through
the new path:
- `FailedTasksCheck` unit-tested directly: claim + settle + `Observation`
  shape (including the rendered `data["body"]` content, FR-1's new coverage)
  + recursion-guard skip.
- `OpenIssueBehavior` unit-tested directly: idempotent marker, error → no
  loop (settles via ordinary `_fail`, not in-behavior handling), the
  `Origin:` footer when `data.source` is present (FR-3's new coverage).
- At least one end-to-end test driving a failed task through
  `ScheduledTrigger` → `SourcePoller` → dispatcher → `heal` → `file-issue`
  with `FakeClock` + in-memory drivers (`MemoryIssueTracker`,
  `FakeAgentRunner`) proving the whole chain composes, asserting on the
  *content* reaching the prompt/issue body — not just that the chain
  completes (architecture-01 §8.2's explicit warning: none of this fails
  loud in a smoke test unless the smoke test specifically exercises it).
- `tests/test_self_heal_e2e.py` (present on disk, not previously named in
  `plan-01.md`) gets the same treatment — check its scenarios against this
  list during design/development and fold in whatever it covers that the
  above doesn't already.

Delete `tests/test_healer.py` and the two `test_architecture.py`
healer-specific tests (FR-7) once their replacements exist; don't leave both
old and new coverage running in parallel past the end of this task.

## Non-functional requirements

- **Exactly-once drain.** `failed/` still drains monotonically — no task
  claimed twice, none silently dropped, none re-entering `failed/` from this
  path (matches today's `Healer` guarantee word-for-word).
- **Idempotency.** Two heal attempts (crash-and-retry) for the same original
  failure file at most one GitHub issue, via the `harness-heal:<id>` marker
  keyed off `data.heal.of`.
- **No new locking primitive.** The check uses `TaskQueue.claim()` exactly as
  every existing claimer does.
- **Fail-fast configuration.** An unknown finisher kind, or a malformed
  `processes/autoheal.json`, fails at `build()`/process-compile time, never
  mid-run.
- **No dispatcher/consumer branch.** Invariants #2/#3 hold throughout: the
  check decides *whether* to observe, the dispatcher decides *where* a `heal`
  task goes, the finisher registry (not a `behavior_for` branch) decides how
  it's finished.
- **Board visibility (net-new, worth calling out as intentional).** A heal in
  progress now has its own visible lifecycle (`todo` → `heal` → `file-issue`
  → `end`) plus the immediate `healed/` settle of the original — a strict
  improvement over today's single opaque `Healer` run, not a behavior parity
  requirement to hide.

## Data model

- **`Observation`** (existing type) — `state_key` = original failed task id;
  `data` = `{"request": <synthesized diagnostic>, "body": <rendered
  markdown>, "reason": str, "history": [...], "original_request": str,
  "source": {...} | absent, "heal": {"of": <failed-task-id>}}`.
- **`Task.data.heal`** — new convention, `{"of": <task-id>}`, stamped on
  every task the `failed-tasks` check produces; read by the check itself
  (recursion guard) and by `OpenIssueBehavior` (idempotency marker source).
- **`Task.data.sink`** — existing convention (ADR-0015 §40), unchanged,
  applicable to heal tasks via the Process's `sink` field.
- **Heal artifact** — unchanged content shape (`# title` + diagnosis +
  proposed change), now written to the generic `.artifacts/<id>/heal-NN.md`
  path instead of a bespoke worktree-root `issue.md`.
- **`processes/autoheal.json`**:
  ```json
  {
    "trigger": {"interval": "30s"},
    "action": {"check": "failed-tasks", "params": {}},
    "target": {"step": "heal"},
    "dedup": "per-state",
    "sink": {"kind": "none"}
  }
  ```
  No process-level `repository` field is needed — FR-2's repo-less decision
  makes that moot (an earlier open question in `plan-01.md`, resolved by the
  design/architecture decision, not by a `compile_process` capability check).

## Interfaces

- `Check.evaluate() -> list[Observation]` — the `failed-tasks` check's sole
  surface, unchanged port.
- `IssueTracker.open_issue(repo, *, title, body, labels, marker) -> IssueRef`
  — unchanged port, now called from `OpenIssueBehavior` instead of
  `Healer._heal`.
- `ConsumerBehavior.run(task) -> BehaviorResult` — `OpenIssueBehavior`'s
  shape, same as `LandingBehavior`.
- `app.build(..., extra_checks=..., processes_root=...)` — the two new
  parameters (FR-6).
- `processes/autoheal.json` — the operator-facing authoring surface, readable
  /writable via the existing `ProcessAdmin` UI with no new admin surface
  needed (`check_names()`/`sink_kinds()` reflect the new `"failed-tasks"`
  registry entry automatically once it's in the `checks` dict
  `FilesystemProcessAdmin` and `FilesystemProcessRepository` both read from —
  confirm both are handed the *same* dict during development, or the admin
  UI's dropdown silently omits the new check).

## Dependencies and scope

**Depends on:** the Process/Check/finisher machinery already on `origin/main`
(ADR-0014/0015/0016), now present in this worktree since FR-0's merge.

**In scope:** FR-1 through FR-8 above (FR-0 already done).

**Out of scope** (unchanged from `plan-01.md`, per the task notes):
- Any change to `IssueTracker`/`GithubIssueTracker`/`MemoryIssueTracker`
  beyond reuse.
- The Observer/Architect/QA processes that will later reuse `open-issue`.
- Any change to `ScheduledTrigger`/`compile_process`/`ProcessAdmin` beyond
  what FR-6 strictly needs (registering the new check kind, the two new
  `build()` parameters). No process-level `repository`/repo-less-step
  capability at the `compile_process` level — FR-2 solved repo-less at the
  `Workspace` layer instead, which needed no port-level `Observation`/process
  schema change.
- The Slack sink's implementation itself (already shipped) — only its use,
  and the wiring-order fix in FR-4, are new.

## Rough plan

1. ~~FR-0: merge `origin/main`~~ — **done** (`9acd4e2`), verified green.
2. Implement `drivers/failed_tasks_check.py` (`FailedTasksCheck`): claim +
   settle-to-`healed/` + recursion-guard skip + rendered-body `Observation`
   emission (FR-1). Unit test against `MemoryTaskQueue`, migrating
   `tests/test_healer.py`'s fixtures.
3. `GitWorkspace.attach`'s repo-less branch (`_attach_repo_less`, FR-2) and
   the one-sentence `_HEALER_PERSONA` edit (FR-2). Add `"heal"`/`"file-issue"`
   to `AGENT_PERSONAS`/`workflows/heal.json`.
4. Implement `behaviors/open_issue.py` (`OpenIssueBehavior`, FR-3); unit test
   idempotency, the error→no-loop path, and the `Origin:` footer.
5. `app.build()`'s `extra_checks`/`processes_root` parameters and the
   `checks`/`process_sources`/`all_sources` wiring (FR-6); make
   `healed_queue`/`failed` unconditional; wire `"open-issue"` into the
   finisher registry.
6. `cli.py`: narrow `_process_sources` to `_process_check_factories`;
   `_declared_sink_kinds` pre-scan called before `build()` (FR-4); rewrite
   `--heal-repo` as the thin generator (FR-5); `harness init` seeds
   `agents/heal.json`/`workflows/heal.json` generically, not
   `processes/autoheal.json`.
7. Delete `healer.py`, `HealConfig`, `_heal_loop` and every reference
   (FR-5); sweep `models.py`'s stale docstrings (FR-7).
8. Rewrite `CLAUDE.md` invariants 24–27 (+ append 35, 39) and write
   `docs/adr/0018-healing-as-a-process.md` (FR-7).
9. Migrate `tests/test_healer.py` and `tests/test_self_heal_e2e.py` into the
   new shape (FR-8); delete the two now-obsolete `test_architecture.py`
   healer tests once their replacement/narrowed check exists.
10. Full `.venv/bin/pytest -q` + `test_architecture.py` guards green; update
    the README's self-healing section to describe the Process-based setup.

## Open questions

None blocking — every question `plan-01.md` left open was resolved by the
prior `design-01.md`/`architecture-01.md` pass and is carried forward above
as a decision, not reopened:
- Queue-wiring ordering → resolved: compile inside `build()` (FR-1/FR-6).
- Repo-bearing vs. repo-less `heal` → resolved: repo-less (FR-2).
- `--heal-repo` fate → resolved: thin generator, kept (FR-5).
- Terminal queue name → resolved: keep `healed/` (unchanged throughout).
- Process-level `repository` field → resolved: not needed, moot given the
  repo-less decision (Data model, above).

The one thing genuinely still open, deferred to development by design
(not a gap): the exact module home for the moved `_failure_reason`/
`_consumer_history`/`_title`/`_body` helpers — inline in
`drivers/failed_tasks_check.py`/`behaviors/open_issue.py`, or a small shared
`heal_report.py` both import. `architecture-01.md §3.3` explicitly leaves
this as an implementation-level choice ("either are architecture-clean per
invariant 17"); no need to force a decision at the plan level.
