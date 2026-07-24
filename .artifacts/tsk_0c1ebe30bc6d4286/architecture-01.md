# Architecture: convert self-heal `Healer` into a Process

Grounded by reading the real, current state of every file cited below directly off
`origin/main` (`9ffd0db`) — this worktree is still pre-merge (FR-0 not yet done; see
Prerequisites). `origin/main` moved one commit (`9281f79`, docs-only) past the `1ef16f1`
the design doc read; nothing source-relevant changed in between, so the design's file/line
grounding stands. This document accepts the plan's and design's decisions where they hold
up against the real source, and resolves three gaps I found by tracing the actual code paths
the design doc flagged as unconfirmed or didn't fully trace: the sink-wiring order, an
invariant-#39 conflict, and a prompt/artifact-path mismatch the design's FR-3 sketch glossed
over. Everything here is a direct, resolved instruction — no options left open for the
development step to guess at.

## 1. Alignment with existing patterns

The target shape is not new to invent — it is the shape ADR-0014/0015/0016 already built,
extended by exactly one new check kind, one new finisher kind, and one new workflow. The
precedent this task rides is `github-issues`/`github-conflicts`:

- **Check as a driver, factory closed over dependencies at wiring time** —
  `drivers/github_issues_check.py`/`drivers/github_conflicts_check.py` are the shape
  `drivers/failed_tasks_check.py` follows: a `Check` implementation that is *not* in the
  dependency-free `BUILTIN_CHECKS` (`drivers/checks.py`), registered instead through a
  `checks` dict assembled at wiring time and merged into
  `FilesystemProcessRepository.build(checks=...)`. §2 below is the one place this task
  departs from precedent (the *location* of that wiring), not the pattern itself.
- **Finisher as data, registry resolved in `build()`** — `app.py:520-554` is the exact,
  already-built seam ADR-0016 shipped: `finisher_registry: dict[str, ConsumerBehavior]`
  defaults to `{"open-pr": landing}`, a caller-supplied `finishers=` dict is merged over it,
  `behavior_for()` looks the step's bound kind up in that registry with **no branch on step
  name**, and an unknown kind fails at `build()` (`app.py:549-554`), never mid-run. Adding
  `"open-issue"` → `OpenIssueBehavior` is one more entry, exactly as designed.
- **A `ConsumerBehavior` never handles its own errors** — `LandingBehavior`'s `ForgeError`/
  `GitError` are not caught inside the behavior; `Consumer.tick()` (`consumer.py:70-78`)
  already wraps `await self._behavior.run(task)` in a blanket `except Exception` and calls
  `_fail`, transferring the task to `failed/` with a `"failed"` event carrying the full task.
  `OpenIssueBehavior` needs **zero** try/except for the same reason — confirmed by reading
  `consumer.py` directly, not assumed.
- **`Workspace.attach`/`MemoryWorkspace.attach` already tolerate a repo-less task in one of
  the two drivers** — `drivers/memory.py:212-217` keys purely on `task.id`, never touches
  `task.repository`. Every in-memory unit/e2e test already exercises a repo-less path today;
  only `GitWorkspace` (`drivers/git_workspace.py`) needs a new branch.
- **Invariant #35's exception is precedent, not novelty** — `TaskSource.poll()`'s own
  docstring (`ports/source.py:71-76`) already sanctions "an idempotent, side-effecting action
  per polled item that produces no task" (`GithubTaskSource.poll()` swapping a label). The
  `failed-tasks` check's claim-and-settle of a *pre-existing* task off `failed/` is the same
  category, not a new one.

## 2. The two decisions the plan/design already made — affirmed, with the reasoning re-verified

### 2.1 Repo-less `heal` step: `Workspace.attach` tolerates `task.repository is None` — **affirmed**

Verified against `ports/repos.py` (`resolve(name: str) -> Path`, no `None` case),
`ports/workspace.py` (`attach(task) -> WorkspaceHandle`, no repository-optionality
documented today) and `drivers/git_workspace.py:241-296` (`attach` calls
`self._registry.resolve(task.repository)` unconditionally at the top, before any worktree
logic). The design's Option 2 is correct and is the only change needed in `GitWorkspace`:

```python
def attach(self, task: Task) -> GitWorkspaceHandle:
    if task.repository is None:
        return self._attach_repo_less(task)   # new: git-init a standalone repo at
                                                 # <worktrees_root>/<task_id>, no registry lookup
    override = task.data.get("branch")
    ...
```

Insert this branch **before** `self._registry.resolve(...)` (currently the second line of
`attach`), not after — `resolve` must never be called with `None`. `_attach_repo_less`
mirrors the existing non-override create path (`git init`, first commit if needed) plus the
existing reset-on-reattach behavior (`reset --hard` + `clean -fd`) against its own root
commit instead of a shared repo's HEAD — no new primitive, a narrower application of the
same one. `push()` is genuinely never called for a repo-less task (its workflow terminates
at `file-issue`, never reaches `land`), so no "no remote configured" branch is needed in
`push()` itself — document this as an implicit contract in `GitWorkspace`'s module
docstring: *a repo-less task's workflow must end before any step that pushes.*
`RepositoryRegistry` and `ClaudeCliBehavior` need **zero** changes — confirmed against
`behaviors/agent.py:71-108`: `attach()` is called unconditionally at line 73 with no
inspection of `task.repository`, so the branch belongs exactly where the design put it,
nowhere else.

### 2.2 `--heal-repo` as a thin generator (Option b) — **affirmed**

Confirmed against `cli.py:1563-1587` (today's `_process_sources`/`--heal-repo` block) and
`FilesystemProcessAdmin.write` (`drivers/fs_processes.py:333-345`, which validates through
the identical `compile_process` the repository runs, so a generated file can't be malformed
by construction). `--heal-repo <owner/repo>` becomes: add `"heal"` to `served_names`, build
`issue_tracker` exactly as today, construct `OpenIssueBehavior` and hand it via `build()`'s
pre-existing `finishers=` override, and write `processes/autoheal.json` via
`FilesystemProcessAdmin` if absent. No new `build()` parameter for this piece — the
extension point already exists and is already tested
(`test_app.py::test_caller_supplied_finisher_registry_entry_is_used`, confirm this test name
survives the merge and extend it, don't duplicate its shape).

## 3. Three gaps closed (the design doc flagged two of these as unconfirmed; the third it missed)

### 3.1 The process-compilation relocation breaks Slack-sink wiring order — resolved

The design's §2 (move process-compilation from `cli.py` into `app.build()`, called after
`events`/`failed`/`healed` exist) is the right call for `FailedTasksCheck` — verified
correct by reading `app.py:441-473`: `events = CompositeEventSink(events,
ProjectionSink(projection), stage_output, SourceReflectorSink(sources))` is constructed at
line 441, and `failed`/`healed` don't exist until after it. `FailedTasksCheck` needs the
*final* `events` and the *real* `failed`/`healed` `TaskQueue` instances, so its factory must
close over them post-construction — inside `build()`, not in `cli.py`.

But the design doc explicitly left one consequence untraced ("this design doc hasn't traced
that driver in full"): **`cli.py::_slack_sinks` reads the *compiled* `process_sources` list
to decide whether to construct a `SlackWebhookSink`, and that `SlackWebhookSink` must be
present in the `sources` list *before* `build()` constructs `SourceReflectorSink(sources)`
at line 441** — `SourceReflectorSink` is handed a plain list reference at `__init__` and
never re-reads it from anywhere else. Once process-compilation moves inside `build()`
(happening *after* line 441 per the design's own placement, right before `pollers = [...]`
at line 610), `cli.py` can no longer inspect `process_sources` before calling `build()` to
decide whether to build the `SlackWebhookSink` — that data doesn't exist yet at that point in
the caller's flow. Reading `design.md §3.5`'s own recommendation ("keep `_slack_sinks` in
`cli.py`, called *after* `build()` returns, reading `harness.pollers`") does not fix this: by
the time `build()` returns, `SourceReflectorSink`'s list is already closed over and
immutable from the caller's side — a `SlackWebhookSink` added to `sources` after the fact
never reaches it. This is a real break, not a hypothetical one; left unresolved, an autoheal
process declaring `"sink": {"kind": "slack"}` would silently never post to Slack when
`--heal-repo` is used (FR-4 acceptance would fail).

**Resolution — decouple the two concerns that only look coupled:**

1. **`_slack_sinks`'s decision doesn't need compiled `ScheduledTrigger`s — it only ever reads
   `getattr(source, "sink", None)`, i.e. the process's raw declared sink kind.** That's `raw`
   JSON data (`{"sink": {"kind": "..."}}`), available by reading `processes/*.json` directly
   with **no** `Check`/`compile_process` involved. Add a small, standalone helper —
   `cli.py::_declared_sink_kinds(processes_root: Path) -> set[str]` — that globs
   `*.json`, `json.loads`s each, and collects `raw.get("sink", {}).get("kind")`, skipping
   unparseable files silently (a broken process file's sink-kind failure surfaces properly,
   loudly, later, when `build()` actually compiles it — this pre-scan's only job is "should a
   `SlackWebhookSink` exist," not validation).
2. **`cli.py` calls this pre-scan *before* `build()`**, builds `SlackWebhookSink` when
   `SLACK_WEBHOOK_URL` is set (unchanged logic, just fed by the pre-scan instead of compiled
   sources; unchanged warning when a process wants `slack` and the env var is absent), and
   passes it in the `sources=` list `build()` already accepts — so it is present when
   `SourceReflectorSink(sources)` is constructed at line 441, exactly as today.
3. **Process compilation still moves inside `build()`**, after `events`/`failed`/`healed`
   exist, exactly per the design's §3.4 — but its output (`process_sources`, the compiled
   `ScheduledTrigger`s, including the new `autoheal` one) is merged **only into the `pollers`
   list**, never backfilled into `SourceReflectorSink`. This is safe and not a regression:
   every `ScheduledTrigger`/`Trigger` subclass inherits `report_progress`/`finish` as no-ops
   (invariant #36, confirmed in `ports/source.py:79-90` and its docstring) — being absent
   from `SourceReflectorSink`'s fan-out is behaviorally identical to being present and
   ignored. `SlackWebhookSink` is the one exception (it is *not* a `Trigger` — it subclasses
   `TaskSource` directly per its own docstring, specifically so it can actually reflect) and
   step 2 above is what gets it into the fan-out correctly.

Net: `build()`'s internal `sources = [*sources, *process_sources]` (design's own snippet,
right before constructing `pollers`) is correct for the poller list but must **not** be
read as "this also reaches `SourceReflectorSink`" — rename the local variable in the
implementation (e.g. `all_sources`) if that helps a future reader avoid the same mistake this
review had to trace through the code to catch.

### 3.2 Invariant #39 ("`build()` gains no parameter") is now false — resolved, must be amended, not silently broken

`CLAUDE.md` invariant #39 (verified verbatim against `origin/main:CLAUDE.md:82`): *"A
Process is a compile-time authoring aggregate, never a runtime object... Nothing under
orchestration imports or names 'process'... **`build()` gains no parameter**, and
`triggers/*.json` is unchanged."* The design's §2/§3.4 (`extra_checks`,
`processes_root` new `build()` parameters) directly contradicts this sentence. This isn't a
test-enforced guard (I checked `tests/test_architecture.py` — no test asserts `build()`'s
signature stays fixed, only `test_fs_processes_is_a_thin_aggregate_over_the_trigger_drivers`,
which restricts `fs_processes.py`'s own imports and is unaffected either way), but it is a
documented, load-bearing claim, and FR-6 already covers rewriting invariants 24-27 — **add
invariant #39 to that same rewrite**, not as a silent contradiction discovered later. Append
(don't delete the historical claim wholesale) a clause:

> *`build()` gained two parameters (`extra_checks`, `processes_root`) when the `failed-tasks`
> check needed to close over ports `build()` itself constructs (the live `events`/`failed`/
> `healed` — see ADR-0018) — a class of dependency `github-issues`/`github-conflicts`
> (external clients, wired entirely in `cli.py`) never had. Process compilation itself is
> still a `cli.py`/`app.py` wiring-time concern; the orchestration core still never imports or
> names "process" — that half of this invariant is unchanged.*

The new ADR-0018 (§5 below) is where the full reasoning belongs; the invariant gets the
one-paragraph pointer.

### 3.3 The persona/artifact contract in the design's FR-3 sketch doesn't match the real conventions — resolved

This is the one gap the design doc didn't flag as unconfirmed — it stated it as settled, but
tracing `behaviors/agent.py::compose_prompt` and `artifacts_layout.py` against the unchanged
`_HEALER_PERSONA` text (`cli.py:362-382`) surfaces two real mismatches:

**a) The failure report has nowhere to go in the generic prompt template.** Today,
`Healer._heal` builds the entire user prompt via the bespoke `heal_prompt()`
(`healer.py:154-201`) — a custom "## Failure report" section (task id, workflow, failing
step, repository, reason, original request) plus a "## What the task did before it failed"
bullet list, assembled from `_failure_reason`/`_consumer_history`/`_request_of`. The new
design runs `heal` through the **generic** `ClaudeCliBehavior`/`compose_prompt()`
(`behaviors/agent.py:111-149`), which only ever reads `task.data["request"]` (a single
line, "Task: ...") and `task.data["body"]` (rendered verbatim if it differs from `request`)
— it has no concept of a structured failure report. Left as the design describes it
("`Observation.data` carries `reason`/`history`/`heal`"), the persona would receive none of
that content in its actual prompt — a silent, severe regression (the healer would diagnose
blind).

**Resolution:** `FailedTasksCheck.evaluate()` must render the failure report into
`Observation.data["body"]` as a markdown string — reusing `_failure_reason`/
`_consumer_history`/`_request_of` (moved verbatim into the check module, or a small shared
`heal_report.py`; both are architecture-clean per invariant 17/`test_architecture.py`'s
driver-import rules, since neither imports beyond `models`) to build the exact same "##
Failure report" + "## What the task did before it failed" content `heal_prompt` builds
today, minus the verdict-format boilerplate (which `compose_prompt` already appends
generically). Keep the **structured** fields (`reason: str`, `history: list[str]`) in
`Observation.data` too — not for the prompt, but because FR-1's own acceptance criteria and
FR-7's test-migration table need them independently inspectable in a `FailedTasksCheck` unit
test without parsing rendered markdown. Set `data["request"]` to a short synthesized
diagnostic line (e.g. `f"Diagnose why task {original.id} failed at step "
f"{original.status!r} (workflow {original.workflow_template!r})."`) so `compose_prompt`'s
first "Task: ..." line reads sensibly — this is new content, not a reuse of the original
task's own request, and should be documented as such in the check's docstring so a future
reader doesn't go looking for it in `heal_prompt`. **Zero changes to `compose_prompt`/
`ClaudeCliBehavior`** — this keeps the generic template genuinely generic (invariant 14),
which is the whole point of routing `heal` through it instead of keeping a bespoke prompt
builder.

Also carry `original.data.get("source")` through into `Observation.data["source"]` when
present (today's `_body()` reads `task.data.get("source")` off the *original* failed task to
add an "Origin: <url>" footer to the filed issue — in the new flow `OpenIssueBehavior` only
ever sees the *fresh* heal task, so this must be explicitly threaded through or that footer
silently disappears), and carry the original's own request text separately as
`Observation.data["original_request"]` (distinct from the synthesized `data["request"]`
above) so `OpenIssueBehavior`'s title fallback chain (`# heading` → verdict summary →
`f"Self-heal: {original_request}"` → generic) matches `_title`'s existing chain exactly
rather than falling back to the synthesized diagnostic sentence.

**b) The persona's hardcoded `issue.md` conflicts with the generic artifact-attempt
convention — and the design's own FR-3 wording ("`.artifacts/<id>/heal-NN/issue.md`") matches
neither.** `artifacts_layout.py` is explicit and is the single source of truth both sides
must obey: an artifact is a **flat file** `.artifacts/<task_id>/<step>-<NN>.md` (see
`next_attempt`/`STEP_ATTEMPT`, `artifacts_layout.py:242,250-268`) — never a per-attempt
*directory* containing a fixed filename. `compose_prompt` tells the agent, generically for
every step: *"Write your output for this step to the file
`.artifacts/{task.id}/heal-01.md`"* (line 138, computed by `next_attempt`). But the unchanged
`_HEALER_PERSONA` text separately, specifically instructs: *"write a proposed GitHub issue to
the file `issue.md` **in your working directory**"* (worktree root, not under `.artifacts/`
at all) — the same conflict the design's own §3.3 restates as fact
("`.artifacts/<id>/heal-NN/issue.md`", a path shape that exists nowhere in the codebase).
Left as-is, the agent gets two contradictory file-writing instructions in one prompt.

**Resolution — edit the persona's file-instruction sentence only, nothing else about its
judgment/verdict logic:** change
*"write a proposed GitHub issue to the file `issue.md` in your working directory"* to
*"write a proposed GitHub issue to the file the harness told you to write your output to
above"* (or equivalent wording that defers to `compose_prompt`'s generic `artifact_relpath`
line instead of hardcoding a filename) — bringing `heal` in line with every other persona in
`AGENT_PERSONAS`, none of which hardcode a filename today. This is a narrow, justified,
one-sentence deviation from "the persona is unchanged" (the FR text's real intent, per
invariants 9/26, is "still only drafts + verdicts, never opens anything" — not "not a single
character may change"; record this narrow edit explicitly in the new ADR so it isn't mistaken
for scope creep). Then:

- `OpenIssueBehavior` reads the draft via `ArtifactView.list(task_id)`, filtering for
  `ref.step == "heal"` and taking the **highest `attempt`** (mirrors how every other
  multi-attempt artifact consumer would pick the latest; `heal` in practice only ever runs
  once per fresh heal task, so this is a single-candidate filter in the common case, not new
  disambiguation logic), then `ArtifactView.read(task_id, "heal", attempt, name)`.
- The `# <title>` / diagnosis / proposed-change **content shape** the persona writes is
  unchanged — only where it goes changes, from a bespoke root-relative path to the generic
  attempt-indexed one every other step already uses. This is a genuine simplification over
  the plan's original framing, not just a fix: `heal`'s deliverable is now visible in the
  standard artifacts UI like every other step's output, which it wasn't before (scratch-dir
  files were never board-visible).

## 4. Proposed architecture

### 4.1 Components (new/changed)

| Component | Kind | Home | Depends on |
|---|---|---|---|
| `FailedTasksCheck` | `Check` (driver) | `drivers/failed_tasks_check.py` | `ports.queue.TaskQueue` (×2: failed, healed), `ports.events.EventSink`, `ports.clock.Clock`, `harness.models` |
| `heal` step (agent) | data (`AgentSpec`, persona text) | `agents/heal.json`, `AGENT_PERSONAS["heal"]` in `cli.py` | none — driven generically by the existing `ClaudeCliBehavior` |
| `heal` → `file-issue` workflow | data | `workflows/heal.json` | `Workflow.finishers` (ADR-0016) |
| `OpenIssueBehavior` | `ConsumerBehavior` (behavior) | `behaviors/open_issue.py` | `ports.issues.IssueTracker`, `ports.artifacts.ArtifactView`, `ports.clock.Clock` |
| `processes/autoheal.json` | data | written by `--heal-repo` via `FilesystemProcessAdmin` | `compile_process` (unchanged) |
| `_declared_sink_kinds` | function | `cli.py` | none — raw JSON read, no `Check` machinery |
| `build()` additions | wiring | `app.py` | `extra_checks`, `processes_root` params (§3.2) |

Nothing here introduces a new port. `Check`, `ConsumerBehavior`, `TaskQueue`, `EventSink`,
`IssueTracker`, `ArtifactView` all exist today; this task is entirely new *implementations*
of existing ports plus data (JSON files), which is exactly the shape ADR-0014/0015/0016
intended new automations to take.

### 4.2 `FailedTasksCheck` — contract

```python
class FailedTasksCheck(Check):
    def __init__(self, *, failed: TaskQueue, healed: TaskQueue,
                 events: EventSink, clock: Clock) -> None: ...
    def evaluate(self) -> list[Observation]: ...
```

Per `evaluate()` call, claim **every** task currently in `failed/` (loop `failed.list()` at
entry — a snapshot, not a live re-poll mid-loop — and `failed.claim(task, new_lock_id())`
each; a lost race per invariant/precedent in `test_lost_claim_race_is_a_noop` returns `None`
and is skipped, not an error). Per successfully claimed task:

1. **Recursion guard first.** `if task.data.get("heal") is not None:` → settle straight to
   `healed/` with history summary `"heal-failed: the heal attempt itself failed"`, emit the
   existing `"healing"`/`"healed"` event pair (reusing today's event names — no event
   consumer needs a rename), produce **no** `Observation`.
2. **Otherwise** → settle to `healed/` with summary `"queued for healing"`, and return one
   `Observation`:
   - `state_key = task.id`
   - `data = {`
     `  "request": <synthesized diagnostic line, §3.3a>,`
     `  "body": <rendered failure-report markdown, §3.3a>,`
     `  "reason": <str, from _failure_reason>,`
     `  "history": <list[str], from _consumer_history>,`
     `  "original_request": <str, from _request_of(original), §3.3a>,`
     `  "source": <original.data["source"] if present, §3.3a>,`
     `  "heal": {"of": task.id},`
     `}`

An empty `failed/` returns `[]` with **no claim attempted** (`FifoStrategy`-free direct
iteration over `failed.list()`; an empty list short-circuits before any `claim()` call — this
is what makes the "no-op cleanly" acceptance criterion trivially true, not something to test
around).

**Where this sits relative to invariant #35's exception:** the check performs one queue
*placement* (`failed/` → `healed/`) as a claiming side effect on a **pre-existing** task; it
never places the *new* task it produces via `Observation` — that's still exclusively
`ScheduledTrigger`/the dispatcher. Same shape as `GithubTaskSource.poll()`'s label swap;
invariant #35 gets exactly the append clause the design proposed, not a rewrite.

### 4.3 `heal`/`file-issue` workflow — two steps, not workflow-less

Affirmed from the design (§3.2 there): a step has exactly one bound behavior, so the persona
(drafts + verdicts) and the deliverable (opens the issue) need distinct step names joined by
a routing edge — the same separation `plan → design → ... → land` already models.

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

`heal` is an ordinary catalog-driven step (`ClaudeCliBehavior`, unchanged), persona per §3.3b.
`file-issue` is bound to the `"open-issue"` finisher — never reached on `request_changes`
(routed straight to `end`), mirroring today's "no action" branch as a router edge instead of
an in-behavior branch (keeps invariant #2 intact).

Any failure at either step (agent exception, `IssueError`) is **not** swallowed — it bubbles
to `Consumer._fail` normally, landing the *fresh* heal task in `failed/`. The
`FailedTasksCheck`'s recursion guard (§4.2 step 1) is the single mechanism that stops this
from looping, covering both failure sites uniformly — a real simplification over today's
`Healer._heal`, which needs a bespoke blanket `try/except` to get the same guarantee.
`OpenIssueBehavior` therefore needs **no** error handling of its own (§1, confirmed against
`Consumer.tick`).

### 4.4 `OpenIssueBehavior` — contract

```python
class OpenIssueBehavior(ConsumerBehavior):
    def __init__(self, *, tracker: IssueTracker, repo: str,
                 artifacts: ArtifactView, clock: Clock,
                 labels: tuple[str, ...] = ("harness:self-heal",)) -> None: ...
    async def run(self, task: Task) -> BehaviorResult: ...
```

1. Locate the `heal` step's artifact via `artifacts.list(task.id)` filtered to
   `ref.step == "heal"`, highest `attempt`; `artifacts.read(task.id, "heal", attempt,
   ref.name)` for the content (§3.3b — no hardcoded `issue.md`).
2. `title`/`body` — the exact `_title`/`_body` logic from `healer.py`, moved here verbatim,
   adapted to read `task.data.get("original_request")` (not `_request_of(task)` on the fresh
   task, which would surface the synthesized diagnostic line instead) and
   `task.data.get("source")` (§3.3a — now present because `FailedTasksCheck` copied it
   through).
3. `marker = task.data["heal"]["of"]` — the **original** failed task's id, carried through
   `ScheduledTrigger._task_for`'s `data = {**obs.data}` merge (`scheduled_trigger.py:83`,
   confirmed unconditional — nothing strips `heal` before the merge).
4. `ref = self._tracker.open_issue(self._repo, title=title, body=body, labels=self._labels,
   marker=marker)` — no try/except (§1/§4.3).
5. `return BehaviorResult(Outcome.DONE, f"opened issue {ref.url}")`.

### 4.5 `app.build()` changes

- **Remove:** `HealConfig` dataclass, the `heal` parameter, the healer/`healed_queue`
  construction block (`app.py:626-655`), the `Healer` import, `Harness.healer`,
  `Harness._heal_loop`, the conditional `_heal_loop` entry in `run()`'s `loops` list
  (`app.py:267`), the `if self.healer is not None` branch in `recover()` (`app.py:196-197`).
- **`healed` becomes an unconditional, always-built terminal queue** (drop the `heal is not
  None` gate on `include_healed=` at `app.py:434` and on the `healed_queue` construction).
  `Harness.recover()` unconditionally includes `failed` in its recovered-queue list too
  (drop the `if self.healer is not None` guard at line 196 — `failed/` is now always a
  potentially-consumed queue; recovering an idle `.processing/` is free, matching the
  existing comment's own reasoning for why `done/` is unconditionally recovered).
- **Add** `extra_checks: dict[str, CheckFactory] | None = None` — merged over
  `BUILTIN_CHECKS`; `cli.py`'s `github-issues`/`github-conflicts` factories move here as data
  (§4.6), unchanged in content.
- **Add** `processes_root: Path | None = None` — defaults to `layout.processes`.
- Right before the existing `pollers = [...]` construction (`app.py:610-612`), after
  `events`/`failed`/`healed_queue`/`known_steps` all exist:

  ```python
  checks = {
      **BUILTIN_CHECKS,
      **(extra_checks or {}),
      "failed-tasks": lambda params: FailedTasksCheck(
          failed=failed, healed=healed_queue, events=events, clock=clock
      ),
  }
  process_repo = FilesystemProcessRepository(processes_root or layout.processes)
  process_sources = process_repo.build(
      clock=clock, checks=checks, repository=None,
      worktree_root=str(layout.worktrees), known_targets=set(known_steps),
  )
  all_sources = [*sources, *process_sources]   # feeds pollers only — see §3.1
  pollers = [
      SourcePoller(source=source, inbox=inbox, events=events) for source in all_sources
  ]
  ```

  `healed_queue` must therefore be constructed **unconditionally**, earlier in `build()` than
  today's conditional block — move its construction up next to `failed`/`done`/`archived`
  (around `app.py:448-450`), before the `checks` dict above needs it.
- `issue_repo`/`issue_tracker` params: **do not add them to `build()`.** §2.2/`cli.py`
  already constructs `OpenIssueBehavior` directly and hands it via the pre-existing
  `finishers=` override — `build()` doesn't need to know about `IssueTracker`/`ArtifactView`
  wiring for this one behavior. `build()`'s only new parameters are `extra_checks` and
  `processes_root`.

### 4.6 `cli.py` changes

- `_process_sources` narrows to just assembling and returning the `github_issues_factory`/
  `github_conflicts_factory` dict — it stops calling `FilesystemProcessRepository`/
  `compile_process` and stops importing them. Rename to `_process_check_factories` (mechanical
  rename matching its narrowed job).
- Remove the block that built `process_sources = _process_sources(...)` /
  `sources = sources + process_sources + _slack_sinks(process_sources)`
  (`cli.py:1563-1566`) — process compilation now happens inside `build()`.
- **Add** `_declared_sink_kinds(processes_root: Path) -> set[str]` (§3.1) and call it
  **before** `build()`, feeding `_slack_sinks`'s existing decision logic (unchanged
  otherwise: build `SlackWebhookSink` when `SLACK_WEBHOOK_URL` is set, warn when a process
  wants `slack` and the var is missing). Its result goes into the `sources=` list `cli.py`
  already assembles and passes to `build()`.
- `--heal-repo` handling rewritten per §2.2: add `"heal"` to `served_names`, build
  `issue_tracker` (unchanged token-presence logic), construct `OpenIssueBehavior`, pass via
  `finishers={"open-issue": ...}`, call `extra_checks=_process_check_factories(...)`, call
  `_ensure_autoheal_process(layout)` (new helper: write `processes/autoheal.json` via
  `FilesystemProcessAdmin` **only if it doesn't already exist** — never overwrite an
  operator's hand-edited file; validated by the same `compile_process` the admin already
  runs, so a malformed write is impossible by construction).
- `known_targets` computation (`cli.py:1546-1554`) must include `"heal"` — ensure `served_names`
  is finalized (including the `--heal-repo`-driven append) **before** this block runs, so a
  bare trigger or the autoheal process itself validates against a `known_targets` set that
  actually contains `heal`.
- `harness init` (`cli.py:_write_healer_agent`, lines 489-508): replace with seeding
  `agents/heal.json` through the *generic* `_write_default_agents` path — add `"heal"` to
  `AGENT_PERSONAS`/`AGENT_MODELS` (persona text per §3.3b, model tier `opus`, unchanged) and
  `workflows/heal.json` (§4.3) to the set `harness init` always writes (dormant data,
  mirroring how `workflows/resolver.json` is shipped unconditionally today) — but **not**
  `processes/autoheal.json` itself, which stays gated behind `--heal-repo` (a bare
  `harness init` with no `--heal-repo` has no repo to file issues against; shipping an inert
  autoheal process with no way to satisfy it would just be dead configuration).
  `_write_default_agents`'s `if step == LANDING_STEP: continue` skip must also skip
  `file-issue` (bound to a finisher, needs no agent spec) — generalize to
  `if workflow.finisher_for(step) is not None: continue`, which incidentally removes the
  hardcoded `LANDING_STEP` special case too. Do this generalization; it is strictly less code
  than adding a second named carve-out.

## 5. Data flow (sequence)

**Normal path:** a task fails at any step → `Consumer._fail` → `failed/` (unchanged). Next
`autoheal` tick → `FailedTasksCheck.evaluate()` claims it, sees no `data.heal`, settles it to
`healed/` (`"queued for healing"`), returns one `Observation` with the rendered failure report
(§4.2). `ScheduledTrigger._task_for` mints a fresh task (`workflow_template="heal"`,
`data.heal.of = <original id>`). `SourcePoller` inboxes it (board-visible in `todo`).
Dispatcher routes to `heal` → `ClaudeCliBehavior` runs the persona repo-lessly (§2.1), writes
its artifact via the generic attempt path (§3.3b), returns `done`. Router sends to
`file-issue` → `OpenIssueBehavior` reads the artifact, calls `IssueTracker.open_issue(...,
marker=<original id>)`, returns `done`. Task reaches `end`.

Two board-visible lifecycles now exist per original failure (the immediate `healed/` settle,
and the separate `heal` task's own columns/history) versus today's single invisible-until-done
`Healer` run — a deliberate, positive change: operators get live visibility into an
in-progress heal for the first time. Record this as intentional in the ADR, not as an
unexplained behavior delta a reviewer might flag as a regression.

**Recursion-guard path:** `heal` or `file-issue` fails (agent exception, `IssueError`) →
`Consumer._fail` sends the *fresh* heal task to `failed/`, normally, no special-casing. Next
`autoheal` tick → `FailedTasksCheck` claims it, finds `data.heal` present, settles straight to
`healed/` (`"heal-failed: the heal attempt itself failed"`), emits no `Observation`. Chain
terminates in exactly one extra hop, matching today's "no healing the healer" guarantee
(invariant 25, restated per §6).

## 6. Invariants and ADR (FR-6) — final scope

Rewrite CLAUDE.md invariants **24-27** (as the plan/design specified) **and append to 35 and
39** (this review's additions, §3.1/§3.2/§4.2):

- **24.** `failed/` has one reader — the `failed-tasks` Check (an action of an
  operator-authored Process, typically `processes/autoheal.json`); `healed/` is the
  never-consumed terminal. Both queues are now unconditionally built — with no
  `failed-tasks`-driving process configured, `failed/` simply has no reader.
- **25.** The check produces at most one fresh task per claimed failure and never writes a
  claimed task back to `failed/` — every claim settles to `healed/` in the same `evaluate()`
  call. Recursion is guarded by a marker (`data.heal`), not by construction: a heal task that
  itself fails **does** pass through `failed/` normally (board-visible) before the check's
  next tick retires it without a new `Observation`.
- **26.** The heal deliverable is opened by the `open-issue` finisher (a `ConsumerBehavior`,
  same footing as `open-pr`/`LandingBehavior`), not the LLM — invariant 9 unchanged, new home.
- **27.** `IssueTracker` is touched by the `open-issue` finisher (wired via `build()`'s
  `finishers=`) and `FailedTasksCheck` is touched only as a `Check` registered into
  `app.build()`'s internal checks dict — neither is known to the dispatcher or consumer
  (existing `test_architecture.py` driver-import guards cover this generically; the two
  healer-specific tests — `test_healer_imports_only_ports_models_and_ids`,
  `test_orchestration_does_not_import_issues_or_healer` — must be **deleted**, not adapted,
  since `healer.py` no longer exists; the `harness.ports.issues` half of the second test's
  assertion is still worth keeping as a standalone check that `dispatcher.py`/`consumer.py`
  never import `ports.issues`, mirroring invariant 32/34's shape).
- **35 (append, don't replace).** `FailedTasksCheck`'s claim-and-settle of a *pre-existing*
  task is the same class of "idempotent, side-effecting claim action" `TaskSource.poll()`'s
  docstring already sanctions, distinct from "a trigger places the *new* task it produces" —
  which still belongs to the dispatcher alone, unchanged.
- **39 (append, don't replace).** The clause from §3.2 above, recording why `build()` gained
  `extra_checks`/`processes_root`.

Also update `models.py`'s `FAILED`/`HEALED` docstrings (`models.py:12-22`) — they currently
say *"drained by the `Healer` loop"* / *"the healer reads `failed/`"* by name; these are
source comments, not `CLAUDE.md`, but they're wrong the moment `healer.py` is deleted and
should be swept in the same change, not left as a stale reference for the next reader.

New ADR `docs/adr/0018-healing-as-a-process.md` (confirmed next free number — `0017` is the
last one on `origin/main`). Records: why `Healer` predated the Process idiom and duplicated
it; the repo-less-`heal`-step and `--heal-repo`-thin-shim decisions (§2) with rejected
alternatives; the process-compilation relocation and its narrow scope relative to ADR-0015
(§3.1/§3.2); the deliberate persona-wording edit and why it doesn't count as "the persona
changed" in the invariant-9/26 sense (§3.3b); and the settle-note/heal-outcome decoupling
(§5, "queued for healing" is not the eventual outcome) as a deliberate, recorded trade.
Supersedes the relevant parts of the self-healing design spec by reference (locate the exact
path post-merge — the plan's `docs/superpowers/...self-heal...` guess needs confirming against
the real filename), without deleting it, per `docs/adr/0000-adr-process.md`'s additive
convention (confirmed: ADR-0000 explicitly frames ADRs as one-decision-per-file with
supersession by reference, not deletion).

## 7. Test migration (FR-7) — unchanged from the design's table, one addition

The design's `tests/test_healer.py` → new-home mapping (§8 there) is sound and should be
followed as written, with one addition this review's §3.3 findings require: a
`FailedTasksCheck` unit test asserting the rendered `data["body"]` actually contains the
failure-report markdown (title/reason/history bullets) — not just that `data["reason"]`/
`data["history"]` exist as structured fields — since §3.3a's fix is specifically that the
*rendered* form is what reaches the prompt. And an `OpenIssueBehavior` unit test asserting the
issue body includes an "Origin: ..." line when the original task carried `data.source`,
covering the §3.3a `source` carry-through explicitly (today's `test_healer.py` has no
equivalent case — this is new coverage, not a migrated one, because the original bug this
guards against only exists once the task is re-created rather than operated on directly).

## 8. Prerequisites and risks

1. **FR-0 (merge `origin/main`) is still outstanding — verified, not assumed.**
   `git merge-base HEAD origin/main` is `0c8027b`; `HEAD` (`c40bba0`) is 72 commits behind
   `origin/main` (`9ffd0db`) as of this writing. None of `healer.py`, `ports/triggers.py`,
   `drivers/fs_processes.py`, ADR-0015/0016, or the invariants this document rewrites exist
   in this worktree's working tree yet — every line/path reference above is grounded against
   `origin/main` via `git show`, exactly as the plan and design documents were. **The
   development step's first action must be the merge** (a merge commit, never a rebase —
   invariant #29), before touching a single line of source. Re-verify every line number cited
   here against the post-merge tree; treat this document's line numbers as of-writing
   pointers, not guaranteed-stable offsets.
2. **The three gaps in §3 are the main execution risk if skipped.** Each is a silent
   correctness regression, not a crash: a missing Slack post, a stale invariant nobody
   notices contradicts the new code, or a healer that runs blind (no failure report in its
   prompt) / writes to two places at once. None fail loud in a smoke test unless the smoke
   test specifically exercises them — flag this to whoever writes FR-7's end-to-end test:
   it must assert on the *content* of the rendered prompt/issue body, not just that the
   chain completes.
3. **`_attach_repo_less`'s git operations are new code on a robustness-sensitive path**
   (raw `git init` into a harness-managed directory, reset-on-reattach against a
   self-contained history). Give it the same idempotent-re-entry care as the existing
   override-reattach logic in `GitWorkspace.attach` — assume the directory may already exist
   from a crashed prior attempt, never assume it's empty.
4. **Scope discipline:** `IssueTracker`, `ScheduledTrigger`/`compile_process`/`ProcessAdmin`
   beyond the `extra_checks`/`processes_root` additions, and the Observer/Architect/QA
   processes that will later reuse `open-issue` are all out of scope, per the task notes —
   this review found no reason to widen that boundary.
