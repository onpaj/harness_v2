# Design (rev 2): convert self-heal `Healer` into a Process

Grounded directly against **this worktree's current tree**, `HEAD = 9acd4e2` (FR-0's merge
of `origin/main`, per `plan-02.md`) — not `git show origin/main:<path>` as `design-01.md`
had to, since the merge is now actually in this checkout. Every path/line reference below
was read live off disk while writing this document, not carried over unverified.

This revision **supersedes `design-01.md`**, folding in all three fixes
`architecture-01.md` found (the Slack-sink wiring-order break, the invariant-#39
contradiction, the persona/artifact-path mismatch) as settled decisions rather than
open gaps, and closing one further gap neither prior document fully resolved: how
`OpenIssueBehavior` recovers the agent's verdict summary (`_title`'s `run.summary`
input) once there is no longer a live `AgentRun` object in scope at the point the
issue gets filed. FR numbering matches `plan-02.md` (FR-0 done; FR-1 through FR-8
below).

No UX/UI section: this is a backend wiring change with zero new user-facing surface —
the existing generic Process/board admin UI already renders whatever `ProcessAdmin`/
`BoardView` expose once the new check/finisher kinds are registered.

## 1. The two open questions — resolved (unchanged from design-01/architecture-01, reaffirmed against the real tree)

### 1.1 Repo-less `heal` step → `Workspace.attach` tolerates `task.repository is None`

Confirmed directly: `GitWorkspace.attach` (`src/harness/drivers/git_workspace.py:241-249`)
calls `self._registry.resolve(task.repository)` unconditionally as its **second**
statement (after computing `override`/`branch`), before any worktree logic; `resolve`
(`ports/repos.py`) takes `name: str`, no `None` case. `MemoryWorkspace.attach`
(`drivers/memory.py`) keys purely on `task.id` and already tolerates `task.repository is
None` today — no change needed there; only `GitWorkspace` needs a new branch.

**Change**, inserted as the *first* statement of `attach` (before `override`/`branch`/
`resolve` are computed — `resolve` must never see `None`):

```python
def attach(self, task: Task) -> GitWorkspaceHandle:
    if task.repository is None:
        return self._attach_repo_less(task)
    override = task.data.get("branch")
    branch = override or f"harness/{task.id}"
    base = self._registry.resolve(task.repository)
    ...  # unchanged
```

`_attach_repo_less` mirrors the existing non-override create/reattach shape, but against
a **standalone** repo instead of a linked worktree off a registered base:

```python
def _attach_repo_less(self, task: Task) -> GitWorkspaceHandle:
    branch = f"harness/{task.id}"
    worktree = self._worktrees_root / task.id
    if not worktree.exists():
        worktree.mkdir(parents=True, exist_ok=True)
        _git(["init", "-q", "--initial-branch", branch, str(worktree)])
        # An empty repo has no HEAD commit yet — reset-on-reattach needs one to
        # reset *to*. Committing here, not deferring to the behavior's own
        # commit(), keeps `_attach_repo_less` idempotent on its own: a second
        # `attach()` call (crash-and-retry before anything was ever written)
        # finds a repo that already satisfies "has a HEAD", same as the
        # registered-repo path always did (a real repo always has a HEAD).
        _git(
            ["-C", str(worktree), "commit", "--allow-empty", "-q", "-m", "root"],
            env_extra=_IDENTITY,
        )
    else:
        # Same reset-on-reattach primitive as the ordinary create path
        # (git_workspace.py:320-327) — just against this repo's own root
        # commit instead of a shared repo's HEAD.
        _git(["-C", str(worktree), "reset", "--hard", "HEAD"])
        _git(["-C", str(worktree), "clean", "-fd"])
    return GitWorkspaceHandle(worktree, branch)
```

`push()` is genuinely never invoked for a repo-less task — its workflow (`heal` →
`file-issue` → `end`, §3.2) never reaches a `land`/`open-pr` step — so no "no remote
configured" branch is needed inside `push()` itself. Document this as an **implicit
contract** in `GitWorkspace`'s module docstring: *a repo-less task's workflow must end
before any step that pushes.* `RepositoryRegistry` and `ClaudeCliBehavior` need **zero**
changes — confirmed against `behaviors/agent.py:45`, which calls
`self._workspace.attach(task)` unconditionally with no inspection of `task.repository`.
Invariant 15 ("`task.repository` is a name, not a path") still holds; it is now
explicitly allowed to be *absent*, with a well-defined, minimal meaning.

`git init --initial-branch <branch>` needs a git ≥2.28 (already implied elsewhere in this
codebase's git usage — no new floor). If the development step finds an older git in CI,
fall back to `git init -q` then `git symbolic-ref HEAD refs/heads/<branch>` before the
first commit; either shape produces the same result (`HEAD` on the task branch from the
very first commit, no orphan default-branch cleanup needed).

**Robustness note carried forward from `architecture-01.md §8.3`:** this is new code on a
robustness-sensitive path (raw `git init` into a harness-managed directory). Give
`_attach_repo_less` the same idempotent-re-entry care as the existing reattach logic —
never assume the directory is empty; the two-branch shape above (`exists()` → reset,
else → init+commit) is that care, not an afterthought.

### 1.2 `--heal-repo` → thin generator over the same knobs any Process-driven workflow uses

Because `OpenIssueBehavior` needs an explicit `repo: str` to call
`IssueTracker.open_issue(repo, ...)` against, and a repo-less `heal` task carries no
`task.repository` to derive one from, some wiring-time constant is unavoidable — exactly
as `HealConfig.repository` already is today (`app.py:77`). `--heal-repo <owner/repo>`
survives as that knob, implemented as pure data/wiring:

1. Add `"heal"` to `served_names` (mirrors the resolver's `resolver_defined` pattern,
   `cli.py:1537-1539`).
2. Build `issue_tracker` exactly as today (`GithubIssueTracker` if `GITHUB_TOKEN`, else
   `MemoryIssueTracker` — `cli.py:1581-1586`, content unchanged) and construct
   `OpenIssueBehavior` directly, handed to `build()` via the **pre-existing**
   `finishers: dict[str, ConsumerBehavior] | None` override — confirmed live at
   `app.py:370,525-526` and already exercised by
   `tests/test_app.py::test_caller_supplied_finisher_registry_entry_is_used`
   (`tests/test_app.py:941`). **No new `build()` parameter for the finisher itself.**
3. Write `processes/autoheal.json` via `FilesystemProcessAdmin(layout.processes).write(...)`
   — confirmed idempotent-safe by reading `drivers/fs_processes.py:333-345`: `write`
   validates through the identical `compile_process` the repository runs at startup, so a
   generated file can't be malformed by construction — but only when the file **does not
   already exist** (never clobber an operator's hand-edited process).

`harness init` (`cli.py:_init`, currently lines 131-181) ships `workflows/heal.json` and
`agents/heal.json` **unconditionally** (dormant data, exactly like `workflows/resolver.json`
is shipped unconditionally today via `RESOLVER_DEFINITION` at `cli.py:113-122`) but does
**not** ship `processes/autoheal.json` — see §6.3 below for why that file stays gated
behind `--heal-repo` specifically, unlike the resolver workflow.

## 2. Why process-compilation moves from `cli.py` into `app.build()`, and how the Slack sink survives the move

### 2.1 The move itself (affirmed from design-01 §2 / architecture-01 §3.1)

`FailedTasksCheck` needs the harness's *own*, live `failed`/`healed` `TaskQueue` instances
and the *live* `EventSink` (the one `ProjectionSink` is wired into), so its claim-and-settle
is board-visible immediately, not just after a restart's `hydrate()`. Confirmed against the
real `app.py`:

- `events = CompositeEventSink(events, ProjectionSink(projection), stage_output,
  SourceReflectorSink(sources))` is constructed at `app.py:441-446`.
- `failed` (`app.py:448`) and (post-FR-5) the now-unconditional `healed_queue` are
  constructed after that, alongside `done`/`archived`/`inbox`/`step_queues`.
- `pollers = [SourcePoller(source=source, ...) for source in sources]` is built at
  `app.py:610-612` — the last point before `build()` returns.

**Decision:** move "compile `processes/*.json` into `ScheduledTrigger`s" from
`cli.py::_process_sources` (today `cli.py:749-813`) into `app.build()`, placed right
before that `pollers = [...]` line, so the `"failed-tasks"` check factory can close over
the real `failed`/`healed_queue`/`events` instances — the same closure shape
`github-issues`/`github-conflicts` already use (`cli.py:778-798`), just over internal
ports instead of an external client. No `test_architecture.py` guard restricts this:
those guards constrain `dispatcher.py`/`consumer.py` and `fs_processes.py`'s own imports,
never `cli.py`-vs-`app.py` placement — and `app.py`'s own module docstring already frames
it as "the one place where the ports meet concrete drivers" (`app.py:1`).

Bare `triggers/*.json` (`_scheduled_sources`, `cli.py:721-746`) is **untouched** — it only
ever used `BUILTIN_CHECKS`, never `github-issues`/`failed-tasks`, and stays wired exactly
where it is today; `failed-tasks` is a Process-only capability.

### 2.2 The Slack-sink wiring-order fix — required, not optional (architecture-01 §3.1)

`cli.py::_slack_sinks` (`cli.py:816-836`) today reads the **compiled** `process_sources`
list (`getattr(source, "sink", None)`, i.e. `ScheduledTrigger.sink`) to decide whether to
build a `SlackWebhookSink`, and that sink must be present in the `sources` list handed to
`build()` **before** `SourceReflectorSink(sources)` is constructed at `app.py:441` — a
plain list reference closed over once, never re-read. Once process-compilation moves inside
`build()` (happening *after* line 441, right before `pollers` at line 610 per §2.1),
`cli.py` can no longer inspect a compiled `process_sources` list before calling `build()` —
that data doesn't exist yet at that point in the caller's flow. Left unresolved: an
autoheal process declaring `"sink": {"kind": "slack"}` would silently never post, because
the `SlackWebhookSink` never makes it into `SourceReflectorSink`'s fan-out — a genuine,
previously-flagged FR-4 acceptance failure, not hypothetical.

**Fix — decouple the sink *decision* from process *compilation*:**

1. `_slack_sinks`'s decision doesn't need a compiled `ScheduledTrigger` at all — it only
   ever reads the process's *raw* declared sink kind. That is plain JSON
   (`{"sink": {"kind": "..."}}`), readable directly with **no** `Check`/`compile_process`
   involved. Add `cli.py::_declared_sink_kinds(processes_root: Path) -> set[str]` — globs
   `processes_root.glob("*.json")`, `json.loads`s each, collects
   `raw.get("sink", {}).get("kind")`, silently skips an unparseable file (its real failure
   surfaces later, loudly, when `build()` actually compiles it — this pre-scan's only job
   is "should a `SlackWebhookSink` exist," not validation).
2. `cli.py` calls this pre-scan **before** `build()`, feeds its result into
   `_slack_sinks`'s existing decision logic (unchanged: build `SlackWebhookSink` when
   `SLACK_WEBHOOK_URL` is set; warn when a process wants `slack` and the variable is
   absent), and passes the resulting sink through the `sources=` list `build()` already
   accepts — present at `SourceReflectorSink` construction time, exactly as today.
   `_slack_sinks`'s signature changes from `(process_sources: list[TaskSource])` to
   `(declared_kinds: set[str])`; its body's two behaviors (build-if-env-set,
   warn-if-declared-but-no-env) are otherwise unchanged.
3. Process compilation still moves inside `build()` (§2.1) — but its output
   (`process_sources`, including the compiled `autoheal` trigger) merges **only into the
   local `pollers`-feeding list**, never backfilled into `SourceReflectorSink`. This is
   safe: every `Trigger` subclass (`ScheduledTrigger` included) inherits
   `report_progress`/`finish` as no-ops (invariant #36) — being absent from
   `SourceReflectorSink`'s fan-out is behaviorally identical to being present and ignored.
   `SlackWebhookSink` is the one exception in this picture (it is *not* a `Trigger` — it
   subclasses `TaskSource` directly, specifically so it can actually reflect), and step 2
   is what gets it into the fan-out correctly.

Name the local variable inside `build()` `all_sources` (not `sources`, which the parameter
already shadows) for exactly this reason — so a future reader can't mistake
`all_sources = [*sources, *process_sources]` (feeds `pollers` only) for "this also reaches
`SourceReflectorSink`."

## 3. Component design

### 3.1 `FailedTasksCheck` (new: `src/harness/drivers/failed_tasks_check.py`)

```python
class FailedTasksCheck(Check):
    def __init__(self, *, failed: TaskQueue, healed: TaskQueue,
                 events: EventSink, clock: Clock) -> None: ...

    def evaluate(self) -> list[Observation]: ...
```

Per `evaluate()` call (synchronous, like every `Check` — no `await`, so nothing else can
interleave a claim of `failed/` mid-loop):

1. `candidates = self._failed.list()`. Empty → return `[]` with **no claim attempted**
   (matches `TaskQueue`'s existing "no-op cleanly" shape and the FR-1 acceptance
   criterion trivially).
2. For each candidate, `task = self._failed.claim(candidate, new_lock_id())`; `None` (lost
   race) is skipped, not an error — the exact behavior
   `tests/test_healer.py::test_lost_claim_race_is_a_noop` already exercises for `Healer`,
   ported verbatim onto `TaskQueue.claim`'s documented contract
   (`ports/queue.py:22-23`: "Returns the task with lockId set, or None on a lost race").
3. **Recursion guard first.** `if task.data.get("heal") is not None:` — this claimed task
   is itself a `heal`-workflow task that failed. Settle it straight to `healed/` with
   history summary `"heal-failed: the heal attempt itself failed"`, emit the existing
   `"healing"`/`"healed"` event pair (reusing today's `Healer` event names — no event
   consumer needs a rename), produce **no** `Observation`.
4. **Otherwise**, settle to `healed/` with summary `"queued for healing"` (deliberately
   *not* the eventual heal outcome — §5 records why this is a real, intentional semantic
   change, not an oversight), and return one `Observation`:

```python
Observation(
    state_key=task.id,
    data={
        "request": _diagnostic_request(task),      # synthesized, §3.1a
        "body": _render_failure_report(task),       # rendered markdown, §3.1a
        "reason": _failure_reason(task),             # structured, independently testable
        "history": _consumer_history(task),          # structured, independently testable
        "original_request": _request_of(task),        # §3.1a
        **({"source": task.data["source"]} if "source" in task.data else {}),
        "heal": {"of": task.id},
    },
)
```

`_failure_reason`, `_consumer_history`, `_request_of` are `healer.py:246-267`'s functions,
moved **verbatim** into this module (or a small shared `heal_report.py` — the
implementation-level choice, still deliberately left open below in §9) — both import only
`harness.models`, architecture-clean per invariant 17.

**§3.1a — why `data["body"]`/`data["request"]`/`data["original_request"]` exist (the
architecture-01 §3.3a fix, folded in here as the design, not restated as a found gap):**
`heal` runs through the **generic** `ClaudeCliBehavior`/`compose_prompt`
(`behaviors/agent.py:83-121`), which only ever reads `task.data["request"]` (a single
"Task: ..." line) and `task.data["body"]` (rendered verbatim when it differs from
`request`) — it has no concept of a structured failure report. Without a rendered
`data["body"]`, the persona would receive *no* failure-report content in its actual
prompt at all — a silent, severe regression (the healer diagnosing blind). So:

- `data["body"]` is the rendered markdown — the same "## Failure report" /
  "## What the task did before it failed" content `healer.py::heal_prompt` builds today
  (`healer.py:154-204`), minus the verdict-format boilerplate (`compose_prompt` already
  appends that generically) and minus the "you are the healer" framing sentence (the
  *persona*, not the *report*, says that — §3.2's persona edit keeps that framing there).
- `data["request"]` is a short synthesized diagnostic line, new content, **not** a reuse
  of the original task's own request:
  `f"Diagnose why task {original.id} failed at step {original.status!r} "`
  `f"(workflow {original.workflow_template!r})."` — documented as such in the check's
  docstring so a future reader doesn't go hunting for it in `heal_prompt`.
- `data["reason"]`/`data["history"]` (structured, `str`/`list[str]`) are kept
  **independently**, alongside the rendered `body` — not for the prompt, but because
  FR-1's acceptance and FR-8's test table need them inspectable in a `FailedTasksCheck`
  unit test without parsing markdown back out.
- `data["original_request"] = _request_of(task)` (the **original** failed task's own
  request — `"request"`/`"title"`/`"summary"`, first non-empty), carried through
  **separately** from the synthesized `data["request"]` above, so `OpenIssueBehavior`'s
  title fallback chain (§3.3) matches `healer.py::_title`'s existing chain exactly rather
  than degrading to the synthesized diagnostic sentence.
- `data["source"] = task.data["source"]` when present — `healer.py::_body`
  (`healer.py:231-243`) reads `task.data.get("source")` off the *original* failed task to
  add an "Origin: <url>" footer to the filed issue; in the new flow `OpenIssueBehavior`
  only ever sees the *fresh* `heal` task, so this must be threaded through explicitly or
  the footer silently vanishes.

**Zero changes to `compose_prompt`/`ClaudeCliBehavior`** — keeps the generic template
genuinely generic (invariant 14), which is the entire point of routing `heal` through it
instead of keeping a bespoke prompt builder.

**Invariant #35's exception, restated for this check, not widened:** the check performs
one queue *placement* (`failed/` → `healed/`) as a documented **source-side claiming side
effect**, the same category `TaskSource.poll()`'s own docstring already sanctions
(`ports/source.py`: "an implementation may also perform an idempotent, side-effecting
action per polled item that produces no task" — `GithubTaskSource.poll()`'s label swap is
the existing example). It never places the *new* task it produces via `Observation` —
that stays exclusively `ScheduledTrigger`/the dispatcher.

### 3.2 `heal` → `file-issue`: a two-step workflow (new: `workflows/heal.json`)

**Decided, reaffirmed:** not workflow-less. A step has exactly one bound behavior
(`behavior_for(step)` returns either the finisher registry's entry *or* the catalog-driven
`ClaudeCliBehavior`, confirmed at `app.py:556-583` — never both), so the persona (drafts +
verdict) and the deliverable (opens the issue) need distinct step names joined by a routing
edge — the same separation `plan → design → ... → land` already models.

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

- **`heal`** — an ordinary `ClaudeCliBehavior`-driven agent step. Persona: `_HEALER_PERSONA`
  (`cli.py:362-382`), moved into `AGENT_PERSONAS["heal"]`/`AGENT_MODELS["heal"] = "opus"`
  (`cli.py:387-417`) so it is seeded by the **existing, generic** `_write_default_agents`
  path instead of the bespoke `_write_healer_agent` (`cli.py:489-508`, deleted — §6.3).
  `allowed_outcomes = (done, request_changes)`, unchanged. Runs **repo-less** by default
  (§1.1); nothing here forecloses an operator later handing it a `RepositoryRegistry`-known
  name via a process-level extension, but that is not the default and out of this task's
  scope.

  **Persona edit — required, one sentence only (architecture-01 §3.3b, folded in as the
  design):** `artifacts_layout.py` is the single source of truth for artifact placement —
  a flat file `.artifacts/<task_id>/<step>-<NN>.md` (`next_attempt`/`STEP_ATTEMPT`,
  `artifacts_layout.py`), never a per-attempt *directory*. `compose_prompt` already tells
  the agent, generically, to "Write your output for this step to the file
  `.artifacts/{task.id}/heal-01.md`" (`behaviors/agent.py:110`, computed by
  `next_attempt`). But `_HEALER_PERSONA` separately, specifically instructs: *"write a
  proposed GitHub issue to the file `issue.md` **in your working directory**"* — the
  worktree root, contradicting `compose_prompt`'s line in the same prompt. Fix: replace
  that one sentence with wording that defers to the generic artifact-path line instead of
  hardcoding a filename, e.g.:

  > "When it IS a fixable harness bug: write a proposed GitHub issue to the file the
  > harness told you to write your output to above. Its first line must be a title
  > `# <concise title>`; then a short diagnosis (what failed and why), and a concrete
  > proposed change (which module/contract, and what to do). Finish with the verdict
  > `done`."

  Nothing else about the persona's judgment/verdict logic changes — invariants 9/26's real
  intent ("still only drafts + verdicts, never opens anything") is preserved; this narrow,
  deliberate edit is recorded explicitly in the new ADR (§7) so it doesn't read as scope
  creep. The artifact then lands at the generic, flat, attempt-indexed path every other
  step already uses — a genuine improvement, not just a fix: the heal deliverable becomes
  visible in the standard artifacts UI for the first time (today's scratch-dir `issue.md`
  never was).

- **`file-issue`** — bound to the `"open-issue"` finisher kind (§3.3). Reached only on
  `done`; `request_changes` routes straight to `end`, mirroring today's
  `Healer._heal`'s "no action" branch as a **router edge** instead of an in-behavior
  branch (keeps invariant #2 intact: the workflow, not the behavior, decides this).

Any `heal`/`file-issue` **failure** (agent exception, `IssueError`) is **not** swallowed
in-behavior — it bubbles to `Consumer._fail` (`consumer.py:75-79,117-130`) exactly like
every other step, landing the *fresh* heal task in `failed/` normally. `FailedTasksCheck`'s
recursion guard (§3.1 step 3) is the single mechanism that stops this from looping, one
generic mechanism covering both failure sites, where today's `Healer._heal` needs a
bespoke blanket `try/except` to get the same guarantee. `OpenIssueBehavior` therefore needs
**zero** error handling of its own.

### 3.3 `OpenIssueBehavior` — the `open-issue` finisher (new: `src/harness/behaviors/open_issue.py`)

```python
class OpenIssueBehavior(ConsumerBehavior):
    def __init__(self, *, tracker: IssueTracker, repo: str,
                 artifacts: ArtifactView, clock: Clock,
                 labels: tuple[str, ...] = ("harness:self-heal",)) -> None: ...

    async def run(self, task: Task) -> BehaviorResult: ...
```

`run()`:

1. **Locate the draft.** `refs = [r for r in self._artifacts.list(task.id) if r.step ==
   "heal"]`; pick the highest `attempt` (mirrors the general latest-attempt convention; in
   practice a single candidate for a fresh heal task). `content =
   self._artifacts.read(task.id, "heal", ref.attempt, ref.name) or ""`.
2. **Recover the verdict summary — the one detail neither `design-01.md` nor
   `architecture-01.md` pinned down exactly (this design's addition).**
   `healer.py::_title(run, task, body)` reads `run.summary` — the live `AgentRun` result
   `Healer._heal` had directly in scope. `OpenIssueBehavior.run(task)` receives only
   `task`, no `AgentRun`. But that summary is **not lost** — `Consumer._deliver`
   (`consumer.py:90-98`) already appends it to `task.history` as a `HistoryEntry` with
   `actor="consumer:heal"`, `from_step="heal"`, `summary=result.summary` the moment the
   `heal` step returns `done`, and that history rides on the task through the router into
   `file-issue`. So the equivalent read is:
   ```python
   def _heal_verdict_summary(task: Task) -> str | None:
       for entry in reversed(task.history):
           if entry.from_step == "heal" and entry.actor.startswith("consumer:"):
               return entry.summary
       return None
   ```
   (a one-line, narrower cousin of `healer.py::_consumer_history`'s own filter — confirmed
   against the exact `HistoryEntry` shape `_deliver` writes at `consumer.py:91-98`, no
   guessing).
3. **Title/body**, `healer.py::_title`/`_body` (`healer.py:216-243`) logic, moved here
   verbatim with two substitutions: `run.summary` → `_heal_verdict_summary(task)` (step 2),
   and `_request_of(task)`/`task.data.get("source")` → `task.data.get("original_request")`/
   `task.data.get("source")` (reading the carried-through fields FR-1 stamped, **not** a
   fresh `_request_of(task)` on the `file-issue` task itself, which would surface the
   synthesized diagnostic sentence instead of the original request).
4. `marker = task.data["heal"]["of"]` — the **original** failed task's id, confirmed
   unconditionally carried through `ScheduledTrigger._task_for`'s `data = {**obs.data}`
   merge (`drivers/scheduled_trigger.py:83`) — nothing strips `heal` before it. This is
   what makes idempotency survive a `heal`/`file-issue` retry: the marker is stable across
   however many fresh `heal`-workflow task ids a given original failure ever produces (in
   practice: at most one, since `FailedTasksCheck` claims an original exactly once — the
   retry case that matters in practice is the recursion-guard path, §3.2, which never
   re-attempts filing at all).
5. `ref = self._tracker.open_issue(self._repo, title=title, body=body,
   labels=self._labels, marker=marker)` — **no try/except**: `Consumer.tick()`
   (`consumer.py:75-79`) already wraps `behavior.run()` in a blanket `except Exception` →
   `_fail`, so an `IssueError` here lands the task in `failed/` exactly like an agent
   exception does. FR-1's recursion guard is what stops that from looping — not
   in-behavior error handling — confirmed directly against `consumer.py`, not assumed.
6. `return BehaviorResult(Outcome.DONE, f"opened issue {ref.url}")`.

The `request_changes` ("nothing actionable") verdict path never reaches `file-issue` at
all — the workflow's own routing (§3.2) sends it straight to `end` — so `behavior_for`
still never branches on the verdict, and `OpenIssueBehavior` has no dead branch for that
case.

Registered via `--heal-repo`'s `finishers={"open-issue": OpenIssueBehavior(...)}` override
(§1.2 step 2) — `behavior_for` gains no new branch (invariant #2 intact, confirmed at
`app.py:556-559`: `kind = step_finishers.get(step)`, a dict lookup, not an `if`-chain on
step name); an unknown kind still fails at `build()` (`app.py:549-554`, unchanged); a
`heal` workflow served without `--heal-repo` (so `"open-issue"` absent from the registry)
fails the **same** way — `ValueError` naming the unknown kind, at startup, never mid-run.

### 3.4 `app.build()` changes

- **Remove:** `HealConfig` (`app.py:68-80`), the `heal: HealConfig | None = None`
  parameter (`app.py:374`), the `healer`/`healed_queue` construction block
  (`app.py:626-655`), the `Healer` import (`app.py:34`), `Harness.healer`
  (`app.py:159,168`), `Harness._heal_loop` (`app.py:343-349`), the conditional
  `_heal_loop` entry in `run()`'s `loops` list (`app.py:267`), the
  `if self.healer is not None` branch in `recover()` (`app.py:196-197`).
- **`healed` becomes an unconditional, always-built terminal queue**, moved up to sit
  alongside `failed`/`done`/`archived` (near `app.py:448-450`), *before* the new `checks`
  dict below needs it — dropping the `heal is not None` gate on `include_healed=`
  (`app.py:434`) and on `healed_queue`'s construction. `Harness.recover()`
  unconditionally includes `failed` in its recovered-queue list too (drop the
  `if self.healer is not None` guard at `app.py:196` — `failed/` is now always a
  potentially-consumed queue; recovering an idle `.processing/` is free, matching the
  existing comment's own reasoning for why `done/` is unconditionally recovered,
  `app.py:186-191`). `BoardProjection`'s `include_healed`/`column_order(..., healed=...)`
  parameter (`projection.py:58,90,96,105`) is dropped; `HEALED_COLUMN` joins the tail
  unconditionally — an empty, harmless column when nothing ever heals, exactly like an
  unused workflow step's column already is.
- **Add** `extra_checks: dict[str, CheckFactory] | None = None` — merged over
  `BUILTIN_CHECKS`; `cli.py`'s `github-issues`/`github-conflicts` factories
  (`cli.py:778-798`) move here as data, unchanged in content.
- **Add** `processes_root: Path | None = None` — defaults to `layout.processes`; lets
  tests point at a scratch directory without touching `root`.
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
  all_sources = [*sources, *process_sources]   # feeds pollers ONLY — see §2.2
  pollers = [
      SourcePoller(source=source, inbox=inbox, events=events) for source in all_sources
  ]
  ```

  `repository=None` here is unchanged from today's `_process_sources` call
  (§1.1 — no process gets an implicit repo through this path; `heal`'s repo-less-ness
  falls straight out of it, no special-casing needed). `known_targets=set(known_steps)`
  reuses the `known_steps` set `build()` already computes at `app.py:423-428` (served
  workflow steps ∪ catalog names) — since `known_steps` is computed **before** this
  block and already includes `"heal"` once `catalog.get("heal")` and `workflows/heal.json`
  exist, no extra plumbing is needed inside `build()` itself for the "heal is a known
  target" requirement; that requirement instead falls on `cli.py`'s `served_names`
  finalization (§3.5) for the *bare-trigger* `known_targets` set it computes independently.
- `issue_repo`/`issue_tracker` params for `build()` itself: **not added.** §1.2 already
  has `cli.py` construct `OpenIssueBehavior` directly and hand it via the pre-existing
  `finishers=` override — `build()` doesn't need to know about `IssueTracker`/
  `ArtifactView` wiring for this one behavior. `build()`'s existing `issue_tracker`
  parameter (`app.py:373`) is untouched in shape (still accepted, still unused by
  `build()`'s own body once `HealConfig` is removed) — actually: with `HealConfig`
  removed, `issue_tracker` has **no remaining reader inside `build()`** (its only
  consumer today is the `heal is not None` block being deleted). **Remove it too** —
  keeping an unused parameter around would be dead surface, and it is not part of any
  documented external extension point the way `finishers=` is. `cli.py` keeps
  constructing an `IssueTracker` itself (§1.2 step 2) and passes it straight into
  `OpenIssueBehavior`'s own constructor, never through `build()`.

  **Net: `build()`'s only new parameters are `extra_checks` and `processes_root`; its net
  parameter count is unchanged** (`heal`/`issue_tracker` removed, `extra_checks`/
  `processes_root` added) — worth noting in the ADR since it softens how big this looks
  in a signature diff.

### 3.5 `cli.py` changes

- `_process_sources` (`cli.py:749-813`) narrows: stops calling
  `FilesystemProcessRepository`/`compile_process` (stops importing them too); it now just
  assembles and returns the `github_issues_factory`/`github_conflicts_factory` dict
  (`cli.py:778-798`, content unchanged). Rename to `_process_check_factories` — a
  mechanical rename matching its narrowed job, not a behavior change.
- The `sources = sources + _scheduled_sources(...)` line (`cli.py:1555-1557`, bare
  triggers) is untouched.
- Remove the block that built `process_sources = _process_sources(...)` /
  `sources = sources + process_sources + _slack_sinks(process_sources)`
  (`cli.py:1563-1566`) — process compilation now happens inside `build()` (§2.1).
- **Add** `_declared_sink_kinds(processes_root: Path) -> set[str]` (§2.2) and call it
  **before** `build()`; feed its result into `_slack_sinks(declared_kinds)`'s narrowed
  signature (§2.2 step 2); the resulting sink (if any) goes into the `sources=` list
  `cli.py` already assembles and passes to `build()`.
- `--heal-repo` handling (`cli.py:1568-1587`) rewritten per §1.2:
  ```python
  extra_checks = _process_check_factories(args, root, registry, clock=SystemClock(), client=client)
  finishers: dict[str, ConsumerBehavior] = {}
  if args.heal_repo:
      if not use_agent:
          print("error: --heal-repo needs --agent claude (the healer is a claude agent)",
                file=sys.stderr)
          return 2
      served_names = [*served_names, "heal"] if "heal" not in served_names else served_names
      token = os.environ.get("GITHUB_TOKEN")
      issue_tracker = (
          GithubIssueTracker(HttpGithubClient(token)) if token else MemoryIssueTracker()
      )
      finishers["open-issue"] = OpenIssueBehavior(
          tracker=issue_tracker, repo=args.heal_repo, artifacts=artifact_view,
          clock=SystemClock(),
      )
      _ensure_autoheal_process(layout)
  harness = build(
      root, served_names, ..., extra_checks=extra_checks, finishers=finishers or None,
      sources=sources or None,
  )
  ```
  `_ensure_autoheal_process(layout)`: a small new helper writing, via
  `FilesystemProcessAdmin(layout.processes)`, the JSON in §4.5 under the name `"autoheal"`
  — **only if `layout.processes / "autoheal.json"` doesn't already exist**. Uses the same
  `compile_process` validation `FilesystemProcessAdmin.write` already runs
  (`fs_processes.py:333-345`), so a malformed write is impossible by construction.
- `known_targets` computation (`cli.py:1546-1554`) is still needed for `_scheduled_sources`
  (bare triggers) and is now also correct for `"heal"` **once `served_names` already
  includes it** — §1.2 step 1's append must run *before* `cli.py:1546` computes
  `known_targets`, i.e. reorder the `--heal-repo` block to precede the
  `known_targets`/`_scheduled_sources` block if the rewritten `_run` doesn't naturally put
  it there. `build()`'s own internal `known_targets=set(known_steps)` (§3.4) is
  unaffected either way, since it recomputes independently off `known_steps`, which
  already includes `"heal"` via `catalog.get`/`workflows/heal.json` once those exist.
- `harness init` (`cli.py:_init`, `cli.py:131-181`): add a `HEAL_DEFINITION` module
  constant mirroring `RESOLVER_DEFINITION` (`cli.py:113-122`) — `workflows/heal.json`
  per §3.2 — written unconditionally (`cli.py:158-163`'s pattern, extended by one more
  `if not ...exists(): write(...)` block). After loading it back
  (`FilesystemWorkflowRepository(layout.workflows).get("heal")`, mirroring
  `resolver_workflow` at `cli.py:167-169`), call `_write_default_agents(layout,
  heal_workflow)` alongside the existing two calls (`cli.py:175-176`) — **delete**
  `_write_healer_agent` (`cli.py:489-508`) entirely, now redundant with the generic path
  once `"heal"` is in `AGENT_PERSONAS`/`AGENT_MODELS`. `processes/autoheal.json` itself is
  **not** written by `_init` — stays gated behind `--heal-repo` (§1.2; a bare `harness
  init` has no repo to file issues against, and shipping an inert autoheal process with no
  way to satisfy it would be dead configuration, unlike the resolver workflow which is
  useful dormant data regardless of any flag).
- `_write_default_agents`'s skip condition (`cli.py:476-477`, currently
  `if step == LANDING_STEP: continue`) generalizes to
  `if workflow.finisher_for(step) is not None: continue` — covers `file-issue` (bound to
  the `"open-issue"` finisher, needs no agent spec) the same way it covers `land`, and
  incidentally removes the hardcoded `LANDING_STEP` special case. Do this generalization —
  it is strictly less code than adding a second named carve-out, and it is
  forward-compatible with any future finisher-bound step.

## 4. Data schemas

### 4.1 `Observation` (existing type, reused — `ports/triggers.py`, unchanged)

```python
Observation(
    state_key=<original failed task id>,
    data={
        "request": <synthesized one-line diagnostic, §3.1a>,
        "body": <rendered failure-report markdown, §3.1a>,
        "reason": <str>,                # last history entry carrying a reason
        "history": [...],               # rendered consumer-history bullets (list[str])
        "original_request": <str>,      # the original failed task's own request/title/summary
        "source": {...},                # present only when the original carried task.data.source
        "heal": {"of": <original failed task id>},
    },
    # repository left at its default (None) — repo-less by design, §1.1. `ScheduledTrigger`
    # resolves `obs.repository or self._repository`, and the process is compiled with
    # repository=None, so the emitted task's `task.repository` is None either way.
)
```

### 4.2 `Task.data.heal` (new convention)

`{"of": <task-id>}` — stamped on every task `FailedTasksCheck` produces (i.e. every fresh
`heal`-workflow task). Read by:
- `FailedTasksCheck` itself, on a **future** `evaluate()` call, as the recursion guard
  (`task.data.get("heal") is not None` while claiming from `failed/`).
- `OpenIssueBehavior`, as the `IssueTracker.open_issue(marker=...)` source
  (`task.data["heal"]["of"]`).

### 4.3 `workflows/heal.json` — see §3.2 for the full JSON.

### 4.4 `agents/heal.json` — persona content: `_HEALER_PERSONA` with the one-sentence edit
from §3.2, seeded via the generic `_write_default_agents(layout, heal_workflow)` path
once `"heal"` is added to `AGENT_PERSONAS`/`AGENT_MODELS` (§3.5), rather than the bespoke
`_write_healer_agent`. Model tier `"opus"` (unchanged — diagnosis is conservative-judgment
work, same tier `HealConfig`'s persona ran on today), `allowed_tools = ["Read", "Write"]`
(unchanged).

### 4.5 `processes/autoheal.json` (new; written by `--heal-repo`, not by plain `harness init`)

```jsonc
{
  "trigger": {"interval": "30s"},
  "action": {"check": "failed-tasks", "params": {}},
  "target": {"step": "heal"},
  "dedup": "per-state",
  "sink": {"kind": "none"}
}
```

Target is `{"step": "heal"}`, not `{"workflow": "heal"}` — `compile_process`'s
`_parse_target` (`fs_processes.py:160-181`) requires exactly one of `workflow`/`step`, and
`ScheduledTrigger` requires exactly one of them too (`scheduled_trigger.py:50-51`); either
shape reaches the same dispatcher behavior once `heal` is the workflow's `start` step, but
`{"step": "heal"}` is the more literal reading of "target the `heal` step" and matches
`plan-02.md`'s own data-model sketch — use it, not `{"workflow": "heal"}`, to avoid a
needless second layer of indirection through `Workflow.get("heal").start`.

No process-level `repository` field is needed or exists — FR-2's repo-less decision makes
that moot; `compile_process`/`ProcessFields`/`ProcessAdmin` need no schema change.

### 4.6 Heal artifact — unchanged content shape (`# title` + diagnosis + proposed change,
per the persona edit in §3.2), now written to the generic `.artifacts/<task_id>/heal-NN.md`
path (`artifacts_layout.py`'s `next_attempt`) instead of a bespoke worktree-root `issue.md`,
read by `OpenIssueBehavior` via `ArtifactView.list`/`.read` instead of a raw `Path` read.

### 4.7 `IssueTracker.open_issue` (`ports/issues.py`) — unchanged port/signature/idempotency
contract; out of scope to modify, confirmed unnecessary above.

## 5. Sequence walkthroughs

### 5.1 Normal path

1. A task fails at some ordinary step; `Consumer._fail` transfers it to `failed/` exactly
   as today (`consumer.py:117-130`, unchanged).
2. On its next interval tick, `autoheal`'s `ScheduledTrigger.poll()` calls
   `FailedTasksCheck.evaluate()`. It claims the task, sees no `data.heal` marker, settles
   it to `healed/` with summary `"queued for healing"`, and returns one `Observation`
   carrying the rendered failure report.
3. `ScheduledTrigger._task_for` builds a **fresh** task (`step="heal"`, fresh id,
   `data = {..., "heal": {"of": <original-id>}}`, `repository=None`); `SourcePoller`
   inboxes it, board-visible in `todo`.
4. Dispatcher routes it to `heal`'s queue. `ClaudeCliBehavior` attaches the repo-less
   workspace (§1.1), runs the (one-sentence-edited) `healer` persona, writes the artifact
   at the generic path (§3.2), returns `done`. `Consumer._deliver` appends a
   `consumer:heal` history entry carrying the verdict summary.
5. Router sends it to `file-issue`. `OpenIssueBehavior` reads the artifact, recovers the
   verdict summary from history (§3.3 step 2), calls
   `IssueTracker.open_issue(..., marker=<original-id>)`, returns `done`. Task reaches
   `end`.

Two board-visible task lifecycles now exist for one original failure — the settled
`healed/` entry (immediate, generic) and the separate `heal`-workflow task (its own
columns, its own history, its own eventual outcome) — versus today's single `Healer`-owned
task that never appears on the board mid-flight at all. **This is a deliberate
improvement**, recorded as intentional in the ADR, not an unexplained behavior delta a
reviewer might flag as a regression.

### 5.2 Recursion-guard path

1. Step 4 or 5 above fails instead (agent exception, or `IssueError` bubbling out of
   `OpenIssueBehavior`). `Consumer._fail` sends the *fresh heal task* to `failed/` —
   normal consumer behavior, no special-casing.
2. On the next `autoheal` tick, `FailedTasksCheck` claims it, finds `data.heal` present,
   settles it straight to `healed/` with `"heal-failed: the heal attempt itself failed"`,
   and emits **no** `Observation` — no second heal task, no second issue attempt, chain
   terminates in exactly one extra hop. `IssueTracker`'s own marker-based idempotency is a
   second, independent line of defense if this were ever re-entered by a different
   mechanism, but the recursion guard is what actually prevents it here.

## 6. Consequences / notes carried into implementation

1. **`FilesystemProcessAdmin.check_names()`/`sink_kinds()` do *not* need — and cannot be
   fed — the new `"failed-tasks"` check.** Re-verified directly against the real driver
   (`fs_processes.py:357-361`): `check_names()` is hardcoded to
   `tuple(sorted(BUILTIN_CHECKS))`, with **no** parameter or injected `checks` dict at all
   — it is not merely "not yet passed the right dict" (`design-01.md §6.2`'s framing), it
   structurally cannot reflect `github-issues`/`github-conflicts`/`failed-tasks` without a
   `ProcessAdmin`-port change, which is explicitly out of scope for this task (per the
   task notes and invariant #33's "`AgentAdmin`/`WorkflowAdmin`/`ProcessAdmin` are UI-facing
   admin ports"). **Correction to design-01's own open item:** there is no "confirm both
   are handed the same dict" question to resolve — they structurally never share one. A
   hand-authored or `--heal-repo`-generated `processes/autoheal.json` still works correctly
   at runtime (compiled by `FilesystemProcessRepository`, which *does* receive the merged
   `checks` dict via `app.build()`, §3.4) — only the admin UI's check-kind **dropdown**
   under-lists, a pre-existing gap unrelated to this task, not a regression it introduces.
2. **Repo-less `GitWorkspace.attach`** (§1.1) is new code on a robustness-sensitive path;
   give it the idempotent-re-entry treatment §1.1's sketch already embeds (exists → reset,
   else → init+commit), not an afterthought bolted on later.
3. **`_write_default_agents`'s skip-condition generalization** (§3.5) is adopted, not
   merely recommended, in this revision — it is strictly less code than a second
   hardcoded carve-out and is forward-compatible with any future finisher-bound step.

## 7. Invariants and ADR (FR-7)

Rewrite `CLAUDE.md` invariants **24–27** (numbers preserved, content replaced), and
**append** (don't replace) **35** and **39**:

- **24.** `failed/` has one reader — the `failed-tasks` Check (an action of an
  operator-authored Process, typically `processes/autoheal.json`); `healed/` is the
  never-consumed terminal. Both queues are now **unconditionally** built — with no
  `failed-tasks`-driving process configured, `failed/` simply has no reader, exactly as
  before wiring one up.
- **25.** The check produces at most one fresh task per claimed failure and never writes a
  claimed task back to `failed/` — every claim settles to `healed/` in the same
  `evaluate()` call. Recursion is guarded by a marker (`data.heal`), not by construction:
  a heal task that itself fails **does** pass through `failed/` normally (board-visible)
  before the check's next tick retires it without a new `Observation`.
- **26.** The heal deliverable is opened by the `open-issue` finisher (a `ConsumerBehavior`,
  same footing as `open-pr`/`LandingBehavior`), not the LLM — invariant 9 unchanged, new
  home.
- **27.** `IssueTracker` is touched by the `open-issue` finisher (wired via `build()`'s
  `finishers=`) and `FailedTasksCheck` is touched only as a `Check` registered inside
  `app.build()`'s internal checks dict — neither is known to the dispatcher or consumer.
  The two healer-specific `test_architecture.py` tests
  (`test_healer_imports_only_ports_models_and_ids`,
  `test_orchestration_does_not_import_issues_or_healer`, `tests/test_architecture.py:225,240`)
  are **deleted**, not adapted (their subject no longer exists); keep the still-relevant
  half of the second as a standalone "`dispatcher.py`/`consumer.py` never import
  `ports.issues`" check, mirroring invariants 32/34's shape.
- **35 (append).** `FailedTasksCheck`'s claim-and-settle of a *pre-existing* task is the
  same class of "idempotent, side-effecting claim action" `TaskSource.poll()`'s docstring
  already sanctions, distinct from "a trigger places the *new* task it produces" — still
  the dispatcher's alone.
- **39 (append).** *`build()` gained two parameters (`extra_checks`, `processes_root`) —
  and lost two (`heal`, `issue_tracker`; §3.4) — when the `failed-tasks` check needed to
  close over ports `build()` itself constructs (the live `events`/`failed`/`healed` — see
  ADR-0018) — a class of dependency `github-issues`/`github-conflicts` (external clients,
  wired entirely in `cli.py`) never had. Process compilation itself is still a
  `cli.py`/`app.py` wiring-time concern; the orchestration core still never imports or
  names "process" — that half of this invariant is unchanged.*

Also sweep `models.py`'s `FAILED`/`HEALED` docstrings (`models.py:12-22` currently say
*"drained by the `Healer` loop"* by name) — stale source comments, not `CLAUDE.md`, but
wrong the moment `healer.py` is deleted; fix in the same change.

New ADR `docs/adr/0018-healing-as-a-process.md` (confirmed next free number — `docs/adr/`
still ends at `0017-landing-syncs-base-before-proposing.md` per `plan-02.md`'s own
verification). Records: why `Healer` predated the Process idiom and duplicated it; the
repo-less-`heal`-step decision (§1.1) and the rejected repo-bearing alternative; the
`--heal-repo`-thin-shim decision (§1.2) and the rejected "remove entirely" alternative; the
process-compilation-inside-`build()` relocation (§2.1) and the Slack-sink wiring-order fix
(§2.2) with their narrow scope relative to ADR-0015; the deliberate one-sentence persona
wording edit (§3.2) and why it doesn't count as "the persona changed" in the invariant-9/26
sense; the settle-note/heal-outcome decoupling ("queued for healing" is not the eventual
outcome, §3.1/§5.1) as a deliberate, recorded trade; and the `_heal_verdict_summary` history
lookup (§3.3 step 2) as the mechanism that replaces the live `AgentRun.summary` `Healer`
used to have in scope. Supersedes the relevant parts of
`docs/superpowers/specs/2026-07-21-self-healing-design.md` by reference (confirmed present
on disk at that path post-merge), without deleting it, per `docs/adr/0000-adr-process.md`'s
additive convention.

## 8. Test migration (FR-8)

`tests/test_healer.py`'s eight scenarios (`tests/test_healer.py:68-267`, confirmed by
name against the current tree) map as follows — a distinct-behavioral-guarantee mapping,
not a 1:1 file rename, since the mechanism now splits across `FailedTasksCheck` and
`OpenIssueBehavior`:

| Old scenario | New coverage |
|---|---|
| `test_done_verdict_files_an_issue_and_settles_to_healed` | **Split.** `FailedTasksCheck` unit test: claiming a failed task settles it to `healed/` with `"queued for healing"` and returns one `Observation` carrying the failure report. `OpenIssueBehavior` unit test: given a task with `data.heal.of`, a `consumer:heal` history entry, and a `heal`-step artifact, it calls `tracker.open_issue` with `marker=<of>` and returns `done`. The e2e test (below) proves the two compose. |
| `test_request_changes_settles_without_an_issue` | Router/dispatch-level assertion (part of the e2e test): the `heal` workflow routes `request_changes` straight to `end` per `workflows/heal.json`'s own transitions, and `tracker.opened == []` — `OpenIssueBehavior` is provably never invoked on this path. |
| `test_agent_error_settles_to_healed_and_does_not_loop` | Two-hop e2e test: `heal` step's `FakeAgentRunner` raises → task lands in `failed/` (`Consumer._fail`, standard) → next `FailedTasksCheck` tick recognizes `data.heal`, settles to `healed/` with `"heal-failed"`, no second `Observation`. |
| `test_issue_error_settles_to_healed_and_does_not_loop` | Same two-hop shape, `OpenIssueBehavior`'s `IssueError` propagating instead of the agent raising — proves the *same* recursion-guard mechanism covers both failure sites. |
| `test_empty_failed_queue_is_a_noop` | `FailedTasksCheck.evaluate()` on an empty `failed/` → `[]`, no claim attempted (direct unit test). |
| `test_lost_claim_race_is_a_noop` | `FailedTasksCheck` unit test with a `claim()`-always-returns-`None` fake queue → `[]`, nothing settled, nothing observed (same fake-queue-subclass technique `test_healer.py` already uses). |
| `test_second_heal_of_the_same_marker_returns_the_existing_issue` | `OpenIssueBehavior` unit test, called twice with the same `data.heal.of` marker against a shared `MemoryIssueTracker` → one issue (`IssueTracker`'s own idempotency, unchanged, exercised through the new call site). |
| `test_heal_prompt_carries_the_failure_report` | Becomes a `FailedTasksCheck` unit test asserting `data["reason"]`/`data["history"]` content (structured fields) **and** `data["body"]`'s rendered markdown actually contains the failure-report content (title/reason/history bullets) — the new coverage architecture-01 §3.3a's fix specifically requires, since the *rendered* form is what reaches the prompt, not the structured fields alone. |

**New coverage, not migrated from anything:**
- `OpenIssueBehavior` unit test: the issue body includes an "Origin: ..." line when
  `task.data.get("source")` is present (covers the §3.1a `source` carry-through — the
  original bug this guards against only exists once the task is re-created rather than
  operated on directly, so `test_healer.py` has no equivalent case).
- `GitWorkspace._attach_repo_less` unit/integration test (fits alongside the existing
  `tests/test_smoke_git.py`-style real-git coverage, or a narrower direct test if the
  development step finds a cheaper harness): create path produces a repo with a HEAD
  commit on the task branch; reattach path resets-and-cleans against that same root
  commit, not a shared repo's HEAD.

**`tests/test_self_heal_e2e.py`** (present on disk, confirmed 5 scenarios:
`test_failed_task_is_healed_into_an_issue`, `test_healer_finds_nothing_actionable`,
`test_heal_time_error_does_not_loop_back_to_failed`, `test_no_healer_leaves_the_task_in_failed`,
plus the shared `drive_until_quiet`/`build_harness` fixtures) gets rewritten, not deleted,
onto the new path:
- The first three scenarios become **one** end-to-end test (`FakeClock` + in-memory
  drivers: `MemoryTaskQueue`, `FakeAgentRunner`, `MemoryIssueTracker`, `MemoryWorkspace`)
  driving a failed task through `ScheduledTrigger.poll()` → `SourcePoller` → dispatcher →
  `heal` → `file-issue` → `end`, with variants for the `done`/`request_changes`/agent-error
  outcomes — proving the full chain composes, and (per architecture-01 §8.2's explicit
  warning) asserting on the *content* reaching the prompt/issue body, not just that the
  chain completes.
- `test_no_healer_leaves_the_task_in_failed` becomes "no `failed-tasks`-driving process
  configured (no `processes/autoheal.json` built into the harness) → `failed/` stays a
  dead end, `healed/` stays empty" — the direct restatement of the rewritten invariant 24's
  second sentence, exercised the same way (`build()` called with no matching process, drive
  the loop, assert both queues' final state).

Delete `tests/test_healer.py`, `tests/test_self_heal_e2e.py`'s old content (replaced
in-place per above, not left running in parallel), and the two now-obsolete
`test_architecture.py` healer-specific tests (§7) once their replacements exist — don't
leave old and new coverage running side by side past the end of this task.

## 9. Still genuinely open, deferred to development (not a design gap)

The exact module home for the moved `_failure_reason`/`_consumer_history`/`_request_of`/
`_title`/`_body`/`_heal_verdict_summary` helpers — inline split across
`drivers/failed_tasks_check.py` (the first three) and `behaviors/open_issue.py` (the
latter three), or a small shared `heal_report.py` both import. Both are
architecture-clean per invariant 17 (neither imports beyond `harness.models`); no need to
force a decision at the design level — `architecture-01.md §3.3` already explicitly left
this as an implementation-level choice, and this revision doesn't find a reason to narrow
it further.
