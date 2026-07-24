# Design: convert self-heal `Healer` into a Process

Grounded directly against `origin/main` (`1ef16f1`), read via `git show origin/main:<path>`
since this worktree is still pre-merge (FR-0). File/line references below are to that
tree; re-verify offsets after the merge lands. No UX/UI section: this is a backend
wiring change with zero new user-facing surface — the existing generic Process/board
admin UI already renders whatever `ProcessAdmin`/`BoardView` expose.

## 1. The two open questions from the plan, resolved

### 1.1 Repo-less `heal` step (FR-2) → **Option 2: `Workspace` tolerates `task.repository is None`**

`_process_sources`'s call to `FilesystemProcessRepository.build(..., repository=None, ...)`
(`cli.py:807-813`) hands **every** process-compiled task `repository=None` today — there is
no per-process `repository` field in the `processes/*.json` schema (`compile_process` never
reads `raw.get("repository")`; `ScheduledTrigger.repository` is a single value the *caller of
`build()`* supplies once, for the whole directory). Giving `heal` its own repo (option 1)
would mean either a schema change (a new per-process `repository` field, touching
`compile_process`/`ProcessFields`/`ProcessAdmin` — explicitly flagged as bigger-than-needed
scope) or hardcoding a repo for *every* process compiled from that directory, which would
leak the harness's own repo into unrelated future processes (Observer/Architect/QA — out of
scope). Option 2 has a much smaller, self-contained blast radius: the persona genuinely
doesn't need code (task notes), and `task.repository: str | None` is already the type.

**Change:** `Workspace.attach(task)` tolerates `task.repository is None` by attaching to a
throwaway, unregistered worktree — same `.artifacts/<id>/` convention, same attempt
numbering, same commit mechanics, just not derived from `RepositoryRegistry` and never
pushed. `ClaudeCliBehavior` itself needs **zero** changes — it stays exactly as branch-free
as the plan wanted (`self._workspace.attach(task)` unconditionally); the conditional moves
to where the port doc already says it belongs ("machine-specific... outside the task").

- `MemoryWorkspace.attach` (`drivers/memory.py:212-217`) already ignores `task.repository`
  entirely (keys purely by `task.id`) — **no change needed**, repo-less heal tasks already
  work under every in-memory unit/e2e test today.
- `GitWorkspace.attach` (`drivers/git_workspace.py`) gains one new branch at the very top:
  when `task.repository is None`, skip `RepositoryRegistry.resolve` and the `git worktree
  add ... <registered-repo-root>` step; instead `git init` a fresh, standalone repo directly
  at `<worktrees_root>/<task_id>` (if it doesn't exist yet) — no linked worktree, no shared
  object store, nothing to reset against on reattach beyond its own root commit. Reattach
  (task retried) is then just "directory already exists, still a git repo, `reset --hard` +
  `clean -fd` against its own history" — the *same* reset-on-reattach code path, just with
  a self-contained repo instead of a linked worktree. `push()` is never called for a
  repo-less task (its workflow never reaches a `land`/`open-pr` step), so "no remote
  configured" never surfaces as a runtime error — document this as an implicit contract:
  **a repo-less task's workflow must terminate before any step that pushes.**
- `RepositoryRegistry.resolve` itself is untouched — never invoked when `task.repository is
  None`. Invariant 15 ("`task.repository` is a name, not a path") still holds; it's just now
  explicitly allowed to be *absent*, and absence has a well-defined, minimal meaning.

### 1.2 `--heal-repo` (FR-5) → **Option (b): thin generator/gate over the same knobs any other Process-driven workflow uses**

Resolved together with 1.1's consequence: because the `open-issue` finisher needs an
explicit `repo: str` to call `IssueTracker.open_issue(repo, ...)` against, and a repo-less
`heal` task carries no `task.repository` to derive one from (unlike `Forge`, which reads the
git remote), **some** wiring-time constant is unavoidable — exactly as `HealConfig.repository`
already is today. `--heal-repo <owner/repo>` survives as that knob, but implemented as pure
data/wiring, never a runtime branch:

1. Adds `"heal"` to `served_names` (mirrors the resolver's `resolver_defined` pattern at
   `cli.py:1537-1539`, except gated on the flag, not file-presence alone — see §6.3 for why).
2. Builds `issue_tracker` exactly as today (`GithubIssueTracker` if `GITHUB_TOKEN`, else
   `MemoryIssueTracker`) and constructs the `open-issue` finisher via `build()`'s existing
   `finishers: dict[str, ConsumerBehavior] | None` override (the *exact* extension point
   ADR-0016 built and `tests/test_app.py::test_caller_supplied_finisher_registry_entry_is_used`
   already exercises) — **no new `build()` parameter is needed for the finisher itself.**
3. Writes `processes/autoheal.json` via `FilesystemProcessAdmin(layout.processes).write(...)`
   if it doesn't already exist — idempotent, safe on every `--heal-repo` run, and literally
   what FR-5 asked for ("writes... a `processes/autoheal.json`").

`harness init` ships `workflows/heal.json` and `agents/heal.json` unconditionally (dormant
data, exactly like `workflows/resolver.json` is shipped unconditionally today) but does
**not** ship `processes/autoheal.json` — see §6.3 for why that file must stay gated behind
`--heal-repo` specifically, unlike the resolver.

## 2. Why process-compilation moves from `cli.py` into `app.build()` (the plan's flagged risk)

The plan's own "main open architectural risk": `FailedTasksCheck` needs the harness's *own*
`failed`/`healed` `TaskQueue` instances and the *live* `EventSink` (the one wrapped in
`ProjectionSink`), so that its claim-and-settle is board-visible immediately, not just after
a restart's `hydrate()`. Today `_process_sources` (which assembles the checks dict and calls
`FilesystemProcessRepository.build()`) runs in `cli.py`, **before** `build()` — but `events`
(`CompositeEventSink(events, ProjectionSink(projection), ...)`), `failed`, and `projection`
are all constructed *inside* `build()` (`app.py:441-473`). Two objects independently pointed
at the same `layout.failed`/`layout.healed` directories would be state-safe for the file
operations themselves (`FilesystemTaskQueue.claim`/`transfer` are stateless besides paths —
confirmed by reading `drivers/fs_queue.py`: neither emits an event), but the check's
`events.emit("healed", ...)` call would land on a *different* `EventSink` than the one
`ProjectionSink` is wired into — the board would silently miss the transition until the next
restart. `BoardProjection` is confirmed purely event-sourced after startup hydration
(`projection.py` docstring: "Built from two sources: a one-time hydration... and after that
only from the stream of events") — there is no live re-poll to fall back on.

**Decision:** move the "compile `processes/*.json` into `ScheduledTrigger`s" step from
`cli.py::_process_sources` into `app.build()`, called *after* `events`/`failed`/`healed` exist
internally, so the `failed-tasks` factory can close over the real, board-wired instances —
exactly the same closure pattern `github-issues`/`github-conflicts` already use, just over
internal ports instead of an external client. This is legal against every existing
`test_architecture.py` guard: those guards restrict `dispatcher.py`/`consumer.py` and
`drivers/fs_processes.py`'s own import set — nothing pins process-compilation to `cli.py`
specifically, and `app.py` is already "the one place where the ports meet concrete drivers"
per its own module docstring. `cli.py` keeps building the *external*-client-bearing check
factories (`github-issues`, `github-conflicts` — unchanged) and now merely hands them to
`build()` as data (`extra_checks`) instead of driving `FilesystemProcessRepository` itself.

Bare `triggers/*.json` (`_scheduled_sources`, a separate, lower-level authoring surface) is
**untouched** — it only ever used `BUILTIN_CHECKS`, never `github-issues`/`failed-tasks`, and
stays that way; `failed-tasks` is a Process-only capability, matching the FR text's own
`processes/autoheal.json` framing.

ADR-0015 recorded, as a consequence of that decision, "no change to... `build()`'s
signature" — true for the check kinds it anticipated (client-bearing, external). This design
extends that boundary for exactly one new class of check (queue/event-bearing, internal),
and the new ADR (§7) records why, so ADR-0015 isn't silently contradicted.

## 3. Component design

### 3.1 `FailedTasksCheck` (new: `src/harness/drivers/failed_tasks_check.py`)

```python
class FailedTasksCheck(Check):
    def __init__(self, *, failed: TaskQueue, healed: TaskQueue,
                 events: EventSink, clock: Clock) -> None: ...

    def evaluate(self) -> list[Observation]:
        ...
```

Per `evaluate()` call: claims **every** task currently listed in `failed/` (via the existing
`TaskQueue.claim()` — the same lease/rename primitive every other claimer uses, no new
locking), using a simple `FifoStrategy`-free direct claim loop (no enqueue-strategy needed;
"process all of them" is the FR-1 acceptance shape: two failed tasks in one call → two
observations). For each claimed task:

- **Recursion guard first.** If `task.data.get("heal")` is present (a `{"of": ...}` marker —
  meaning this failed task *was itself* a `heal`-workflow task, i.e. either the `heal` step's
  agent raised, or the `file-issue` step's `IssueError` bubbled), settle it straight to
  `healed/` with history summary `"heal-failed: the heal attempt itself failed"` and
  **do not** produce an `Observation`. `evaluate()` still emits its own settle events
  (`"healing"` on claim, `"healed"` on settle — reusing today's `Healer` event names so no
  event-consumer needs to change) via the injected `events`, so this is fully board-visible.
- **Otherwise**, settle it to `healed/` with summary `"queued for healing"` (deliberately
  *not* the eventual heal outcome — see §5 for why that's a real, intentional semantic
  change from today), and return one `Observation`:
  - `state_key = task.id` (the failed task's own id — `per-state` dedup is a no-op safety
    net here, matching the plan's own reasoning: a task id is already unique, but it's the
    contract `ScheduledTrigger` needs, and it protects a crash-and-retry from firing the
    check mid-settle and observing the same task twice within one tick — see below).
  - `data = {"reason": ..., "history": [...], "heal": {"of": task.id}}` — the exact fields
    `heal_prompt` reads today (workflow, failing step, repository, reason, original request,
    consumer-history bullets), reusing `healer.py`'s `_failure_reason`/`_consumer_history`/
    `_request_of` helpers verbatim (moved into this module or a small shared
    `heal_report.py`; they import only `harness.models`, so either home is architecture-clean).

An empty `failed/` yields `[]` — no claim attempted, matching `AlwaysCheck`/every other
check's "no-op cleanly" shape (FR-1 acceptance).

**Why the claim-and-transfer can't race itself within one `evaluate()` call:** `claim()` is
the atomic primitive; a task claimed by this check is immediately (same call) either settled
to `healed/` or left in `.processing/` only for the duration of the settle — there is no
`await` inside `evaluate()` (it's synchronous, like every other `Check`), so no other
consumer of `failed/` can interleave.

**Deliberate, narrow exception to invariant #35** ("no trigger writes into a step queue... the
dispatcher alone places it"): this check performs a queue *placement* (`failed/` →
`healed/`) as a documented **source-side claiming side effect**, the same category
`TaskSource.poll()`'s own docstring already sanctions ("an implementation may also perform an
idempotent, side-effecting action per polled item that produces no task... `GithubTaskSource.
poll()` swaps a label as part of claiming an issue"). It never places the *new* task it
produces (that's still `ScheduledTrigger`/dispatcher, invariant #35 intact) — it only
retires a *pre-existing*, already-terminal-bound task off a queue nothing else reads. This is
exactly the shape invariant #24 already carved out for the old `Healer`; §7 rewords #24/#35
to name the check instead of the loop, rather than widening the exception's scope.

### 3.2 `heal` workflow (new: `workflows/heal.json`, written by `harness init`)

Must be a **two-step workflow**, not a workflow-less single step as the plan tentatively
floated — a step has exactly one bound behavior (`behavior_for(step)` returns either the
finisher registry's entry *or* the catalog-driven `ClaudeCliBehavior`, never both), so the
persona (drafts `issue.md`, returns a verdict) and the deliverable (reads `issue.md`, opens
the issue) must be **different step names**, connected by a normal routing edge — exactly
how `plan → design → ... → land` already separates "do the work" from "finish it".

```jsonc
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

- **`heal`** — an ordinary `ClaudeCliBehavior`-driven agent step, persona unchanged
  (`_HEALER_PERSONA`, moved from `_write_healer_agent` into `AGENT_PERSONAS`/`AGENT_MODELS`
  under the key `"heal"` so it's seeded by the *existing*, generic `_write_default_agents`
  instead of a bespoke writer — see §4.3). `allowed_outcomes = (done, request_changes)`,
  unchanged. Runs repo-less (§1.1) unless the operator later decides to hand it a
  `RepositoryRegistry`-known name — nothing in this design forecloses that, it's just not
  the default.
- **`file-issue`** — bound to the `"open-issue"` finisher kind (§3.3). Reached only on
  `done` (the "there's an `issue.md` to open" path); `request_changes` routes straight to
  `end`, mirroring exactly how `Healer._heal`'s "no action" branch never touches
  `IssueTracker` today — now expressed as a router edge instead of an in-behavior branch
  (keeps invariant #2 intact: the workflow, not the behavior, decides this).

Any `heal`/`file-issue` **failure** (agent exception, `IssueError`) is **not** swallowed
in-behavior — it bubbles to `Consumer._fail` exactly like every other step (including
`LandingBehavior`'s `ForgeError`/`GitError`, which already work this way), landing the
*fresh* heal task in `failed/` normally. `FailedTasksCheck`'s recursion guard (§3.1) is what
stops this from looping — one generic mechanism covers *both* "an ordinary task failed" and
"a heal attempt itself failed", where today's `Healer` needed a bespoke swallow-every-error
`try/except` inside `_heal` to get the same guarantee. This is a genuine simplification, not
just a relocation: `OpenIssueBehavior` (§3.3) needs **zero** error handling of its own.

### 3.3 `OpenIssueBehavior` — the `open-issue` finisher (new: `src/harness/behaviors/open_issue.py`)

```python
class OpenIssueBehavior(ConsumerBehavior):
    def __init__(self, *, tracker: IssueTracker, repo: str,
                 artifacts: ArtifactView, clock: Clock,
                 labels: tuple[str, ...] = ("harness:self-heal",)) -> None: ...

    async def run(self, task: Task) -> BehaviorResult: ...
```

Mirrors `LandingBehavior`'s shape (a `ConsumerBehavior` bound via the finisher registry, not
a step the dispatcher special-cases):

1. `content = _read_draft(self._artifacts, task.id)` — scans `artifacts.list(task.id)` for
   the `heal` step's `issue.md` (reusing `ArtifactView.list`/`.read`, exactly as `Landing
   Behavior` enumerates artifacts; `heal` runs at most once per attempt here, so "the ref
   named `issue.md`" is unambiguous — no attempt-disambiguation logic needed beyond what
   `next_attempt`/`ArtifactView` already give for free).
2. `title = _title(task, content)`, `body = _body(task, content)` — the exact heading-then-
   fallback logic `healer.py::_title`/`_body` has today, moved here verbatim (adapted from a
   `Path` read to an `ArtifactView` read).
3. `marker = task.data["heal"]["of"]` — **the original failed task's id**, not `task.id`
   (the fresh heal task's own, freshly-generated id) — carried through via `data.heal.of`
   from `FailedTasksCheck` → `ScheduledTrigger._task_for`'s `data = {**obs.data}` merge.
   This is what makes idempotency survive a `heal`/`file-issue` retry: two different heal
   *task ids* for the same original failure (crash-and-retry, since a retry is itself a
   *fresh* task once `FailedTasksCheck` re-claims a re-failed original — though note the
   normal FR-1 path only ever claims an original failed task **once**, so the retry case
   that actually matters in practice is "the `heal`/`file-issue` step itself failed and got
   swept by the recursion guard" — which never re-attempts filing at all, by design).
4. `ref = self._tracker.open_issue(self._repo, title=title, body=body, labels=self._labels,
   marker=marker)` — **no try/except**: an `IssueError` propagates to `Consumer._fail`,
   landing this heal task in `failed/`, swept clean on the next `failed-tasks` tick (§3.2).
5. Returns `BehaviorResult(Outcome.DONE, f"opened issue {ref.url}")`.

Registered in `cli.py`'s `--heal-repo` handling via `build(finishers={"open-issue":
OpenIssueBehavior(...)}, ...)` — the pre-existing extension point, no `build()` signature
change for this piece. `behavior_for` gains no new branch (invariant #2 intact); an unknown
kind still fails at `build()` (unchanged validation loop, `app.py:549-554`); a `heal` workflow
served without `--heal-repo` (so `"open-issue"` absent from the registry) fails the **same**
way — `ValueError` naming the unknown kind, at startup, never mid-run.

### 3.4 `app.build()` changes

- **Remove:** `HealConfig`, the `heal: HealConfig | None = None` parameter, the
  `healer`/`healed_queue` construction block (`app.py:626-655`), `Healer` import,
  `Harness.healer`/`Harness._heal_loop`, the `_heal_loop` entry in `run()`'s `loops` list,
  the `if self.healer is not None` conditional in `recover()` (`app.py:193-197`).
- **`healed` becomes an unconditional, always-built terminal queue**, symmetric with `done`/
  `archived` — no longer gated behind `heal is not None`. `Harness.recover()` unconditionally
  includes `failed` in its recovered-queue list too (dropping the `if self.healer is not
  None` guard — `failed/` is now *always* a potentially-consumed queue, exactly like `done/`
  already is unconditionally recovered "because it is the one write-into queue that also
  gets claimed out of"; recovering an idle `.processing/` is free, per that same comment).
  `BoardProjection`'s `include_healed`/`column_order(..., healed=...)` parameter is dropped;
  `HEALED_COLUMN` joins the tail unconditionally alongside `DONE_COLUMN`/`FAILED_COLUMN` —
  it's an empty, harmless column when nothing ever heals, exactly like an unused workflow
  step's column already is.
- **Add** `extra_checks: dict[str, CheckFactory] | None = None` — merged over
  `BUILTIN_CHECKS` (cli.py's `github-issues`/`github-conflicts` factories move here,
  unchanged in content, just handed in as data instead of driving `FilesystemProcessRepository`
  itself).
- **Add** `processes_root: Path | None = None` — defaults to `layout.processes`; lets tests
  point at a scratch directory without touching `root`.
- **Add** `issue_repo: str | None = None`, keep `issue_tracker: IssueTracker | None = None`
  (renamed in spirit from heal-specific to generic — signature unchanged, just no longer
  threaded through `HealConfig`). When `issue_repo is not None`, `build()` still does **not**
  need to construct `OpenIssueBehavior` itself — that stays a `finishers=` caller
  responsibility (§3.3) so `build()` doesn't need to know about `IssueTracker`/`ArtifactView`
  wiring order for this specific behavior; `issue_repo`/`issue_tracker` params are threaded
  through only if a future direct (non-`--heal-repo`) caller wants `build()` to construct the
  default itself. **Simplification actually adopted:** since `cli.py` already builds
  `OpenIssueBehavior` and hands it via `finishers=`, `build()` doesn't need `issue_repo`/
  `issue_tracker` params *at all* — drop them from this list; they were only ever needed if
  `build()` constructed the finisher itself, and §3.3 already settled that it doesn't. Net:
  **`build()`'s only new parameters are `extra_checks` and `processes_root`.**
- Inside `build()`, after `events`/`failed`/`healed`/`known_steps` exist (i.e., right before
  the existing `pollers = [...]` construction at `app.py:610-612`):
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
  sources = [*sources, *process_sources]
  pollers = [SourcePoller(source=source, inbox=inbox, events=events) for source in sources]
  ```
  `repository=None` here is unchanged from today's call (§1.1 — no process gets an implicit
  repo through this path; `heal`'s repo-less-ness falls straight out of it, no special-casing
  needed).

### 3.5 `cli.py` changes

- `_process_sources` narrows: it stops calling `FilesystemProcessRepository`/`compile_process`
  and stops importing them; it now just assembles and returns the `github_issues_factory`/
  `github_conflicts_factory` dict (rename to something like `_process_check_factories` to
  match its narrowed job — a mechanical rename, not a behavior change).
- The `sources = sources + _scheduled_sources(...) ` line (bare triggers) is untouched.
- The block that built `process_sources = _process_sources(...)`/`sources = sources +
  process_sources + _slack_sinks(process_sources)` (`cli.py:1558-1566`) is **removed** —
  process compilation happens inside `build()` now. The slack-sink wiring (`_slack_sinks`)
  needs the *compiled* `ScheduledTrigger`s (to read `.sink`) to decide whether to warn/build a
  `SlackWebhookSink` — since `build()` now owns compilation, either (a) `build()` returns the
  compiled process sources for `cli.py` to inspect post-hoc (a small, harmless surface
  addition — e.g. `Harness.pollers[i].source.sink` is already inspectable, since `pollers`
  is a public `Harness` attribute), or (b) `_slack_sinks`'s check moves inside `build()` too.
  **Recommendation:** keep `_slack_sinks` in `cli.py`, called *after* `build()` returns,
  reading `[poller.source for poller in harness.pollers]` instead of a pre-built `sources`
  list — a one-line change at the call site, and the sink itself (`SlackWebhookSink`) still
  isn't wired *into* `events` by `build()` (it's a `TaskSource`-side outbound reflector,
  independent of ingestion — confirm this against `SlackWebhookSink`'s actual wiring in the
  architecture/dev step, since this design doc hasn't traced that driver in full).
- `--heal-repo` handling (`cli.py:1568-1587`) rewritten per §1.2:
  ```python
  extra_checks = _process_check_factories(args, root, registry, clock=SystemClock(), client=client)
  issue_repo = None
  finishers: dict[str, ConsumerBehavior] = {}
  if args.heal_repo:
      if not use_agent:
          print("error: --heal-repo needs --agent claude ...", file=sys.stderr)
          return 2
      if resolver_defined_like_check_for_heal_workflow_missing:  # see below
          print("error: ...", file=sys.stderr); return 2
      served_names = [*served_names, "heal"] if "heal" not in served_names else served_names
      token = os.environ.get("GITHUB_TOKEN")
      issue_tracker = GithubIssueTracker(HttpGithubClient(token)) if token else MemoryIssueTracker()
      finishers["open-issue"] = OpenIssueBehavior(
          tracker=issue_tracker, repo=args.heal_repo, artifacts=artifact_view, clock=SystemClock(),
      )
      _ensure_autoheal_process(layout)  # write processes/autoheal.json if absent
  harness = build(..., extra_checks=extra_checks, finishers=finishers or None, sources=sources or None, ...)
  ```
  `_ensure_autoheal_process`: a small new helper writing, via `FilesystemProcessAdmin`,
  `{"trigger": {"interval": "30s"}, "action": {"check": "failed-tasks", "params": {}},
  "target": {"workflow": "heal"}, "dedup": "per-state", "sink": {"kind": "none"}}` under the
  name `"autoheal"` — **only if `layout.processes / "autoheal.json"` doesn't already exist**
  (never overwrites an operator's hand-edited file). Uses the *same* `compile_process`
  validation `FilesystemProcessAdmin.write` already runs, so a malformed write is impossible
  by construction.
- `known_targets` computation (`cli.py:1546-1554`) is still needed for `_scheduled_sources`
  (bare triggers) and is now also naturally correct for `"heal"` once §1.2 step 1 has already
  added it to `served_names` *before* this block runs (reorder if necessary — `served_names`
  must be finalized, including `heal`, before `known_targets` is computed here, otherwise a
  bare trigger targeting `heal` would wrongly fail validation; `build()`'s own internal
  `known_steps` computation is unaffected either way since it recomputes independently).

## 4. Data schemas

### 4.1 `Observation` (existing type, reused — `ports/triggers.py`, unchanged)

```python
Observation(
    state_key=<original failed task id>,
    data={
        "reason": <str>,               # last history entry with a reason
        "history": [...],              # rendered consumer-history bullets (list[str], not markdown)
        "heal": {"of": <original failed task id>},
    },
)
```
No `repository` set on the `Observation` (repo-less by design, §1.1).

### 4.2 `Task.data.heal` (new convention)

`{"of": <task-id>}` — stamped on every task `FailedTasksCheck` produces (i.e. every fresh
`heal`-workflow task). Read by:
- `FailedTasksCheck` itself, on a **future** `evaluate()` call, as the recursion guard
  (`"heal" in task.data` while claiming from `failed/`).
- `OpenIssueBehavior`, as the `IssueTracker.open_issue(marker=...)` source.

### 4.3 `workflows/heal.json` — see §3.2 for the full JSON.

### 4.4 `agents/heal.json` — unchanged persona content (`_HEALER_PERSONA`), seeded via the
generic `_write_default_agents(layout, heal_workflow)` path once `"heal"` is added to
`AGENT_PERSONAS`/`AGENT_MODELS` (§1.2/§6.3), rather than the bespoke `_write_healer_agent`.
`_write_default_agents`'s `if step == LANDING_STEP: continue` skip needs to also skip
`file-issue` (bound to a finisher, needs no agent spec) — **recommend generalizing** the
skip to `if workflow.finisher_for(step) is not None: continue`, which incidentally also
removes the hardcoded `LANDING_STEP` special case. Flagging as a recommended cleanup, not
mandating it — a narrower `step == "file-issue"` carve-out works too if the architecture step
prefers the smaller diff.

### 4.5 `processes/autoheal.json` (new; written by `--heal-repo`, not by plain `harness init`)

```jsonc
{
  "trigger": {"interval": "30s"},
  "action": {"check": "failed-tasks", "params": {}},
  "target": {"workflow": "heal"},
  "dedup": "per-state",
  "sink": {"kind": "none"}
}
```

### 4.6 `issue.md` artifact — unchanged shape (`# title` + diagnosis + proposed change),
written by the `heal` step, now read by `OpenIssueBehavior` via `ArtifactView` instead of a
raw `Path` read off a scratch directory.

### 4.7 `IssueTracker.open_issue` — unchanged port/signature/idempotency contract (out of scope
to modify, confirmed unnecessary above).

## 5. Sequence walkthroughs

### 5.1 Normal path

1. A task fails at some ordinary step; `Consumer._fail` transfers it to `failed/` exactly as
   today.
2. On its next interval tick, `autoheal`'s `ScheduledTrigger.poll()` calls
   `FailedTasksCheck.evaluate()`. It claims the task, sees no `data.heal` marker, settles it
   to `healed/` with summary `"queued for healing"`, and returns one `Observation` carrying
   the failure report.
3. `ScheduledTrigger._task_for` builds a **fresh** task (`workflow_template="heal"`, fresh
   id, `data = {..., "heal": {"of": <original-id>}}`); `SourcePoller` puts it in the inbox,
   board-visible in `todo`.
4. Dispatcher routes it to `heal`'s queue. `ClaudeCliBehavior` runs the (unchanged) `healer`
   persona repo-lessly, writes `issue.md`, returns `done`.
5. Router sends it to `file-issue`. `OpenIssueBehavior` reads `issue.md`, calls
   `IssueTracker.open_issue(..., marker=<original-id>)`, returns `done`. Task reaches `end`.

Two board-visible task lifecycles now exist for one original failure — the settled `healed/`
entry (immediate, generic) and the separate `heal`-workflow task (its own columns, its own
history, its own eventual outcome) — versus today's single `Healer`-owned task that never
appears on the board mid-flight at all. **This is a deliberate improvement**, not an
oversight: operators get live visibility into an in-progress heal attempt for the first
time.

### 5.2 Recursion-guard path

1. Step 4 or 5 above fails instead (agent exception, or `IssueError` bubbling out of
   `OpenIssueBehavior`). `Consumer._fail` sends the *fresh heal task* to `failed/` — normal
   consumer behavior, no special-casing.
2. On the next `autoheal` tick, `FailedTasksCheck` claims it, finds `data.heal` present,
   settles it straight to `healed/` with `"heal-failed: the heal attempt itself failed"`, and
   emits **no** `Observation` — no second heal task, no second issue attempt, chain
   terminates in exactly one extra hop. `IssueTracker`'s marker-based idempotency is a second,
   independent line of defense if this were ever re-entered by a different mechanism, but the
   recursion guard is what actually prevents it here.

## 6. Consequences / things the architecture step should double-check

1. **`_slack_sinks` wiring order** (§3.5) — this design recommends reading `harness.pollers`
   post-`build()` instead of pre-`build()` `sources`; verify `SlackWebhookSink`'s actual
   construction/wiring shape against `origin/main` before committing to that, since this
   design doc traced it only at the docstring level.
2. **`FilesystemProcessAdmin.check_names()`** returns `tuple(sorted(BUILTIN_CHECKS))` only —
   it does **not** include `github-issues`/`github-conflicts` today, and won't include
   `failed-tasks` either without a change explicitly out of scope here (`ProcessAdmin`
   beyond what's strictly needed). The admin UI's check-kind dropdown will under-list; a
   hand-authored/`--heal-repo`-generated `processes/autoheal.json` still works at runtime
   (compiled by `FilesystemProcessRepository`, not gated by the admin's dropdown) — this is a
   **pre-existing gap**, not a regression introduced here, and is called out so it isn't
   mistaken for one during review.
3. **`_write_default_agents`'s skip condition generalization** (§4.4) is a recommendation,
   not a requirement — either shape is acceptable.
4. **Repo-less `GitWorkspace.attach`** (§1.1) is new code on a security/robustness-sensitive
   path (raw `git init` into a harness-managed directory); the development step should give
   it the same care as the existing reset-on-reattach logic (idempotent re-entry, no
   assumption the directory is empty on a crash-and-retry).

## 7. Invariants and ADR (FR-6)

Rewrite CLAUDE.md invariants 24-27 (numbers preserved, content replaced):

- **24.** `failed/` has one reader — the `failed-tasks` Check (an action of an
  operator-authored Process, typically `processes/autoheal.json`); `healed/` is the
  never-consumed terminal. Both queues are now unconditionally built (§3.4) — with no
  `failed-tasks`-driving process configured, `failed/` simply has no reader, exactly as
  before wiring one up.
- **25.** The check produces at most one fresh task per claimed failure, and never writes
  a claimed task back to `failed/` itself — every claim settles to `healed/` in the same
  `evaluate()` call. Recursion ("no healing the healer") is guarded by a marker
  (`data.heal`), not by construction: a heal-workflow task that itself fails **does** pass
  through `failed/` normally (visible on the board) before the check's next tick recognizes
  the marker and retires it without producing a new `Observation`.
- **26.** The heal deliverable is opened by the `open-issue` finisher (a `ConsumerBehavior`,
  same footing as `LandingBehavior`/`open-pr`), not the LLM — invariant 9 unchanged, new
  home.
- **27.** `IssueTracker` is touched by the `open-issue` finisher (wired in `cli.py` via
  `build()`'s `finishers=` override) and `FailedTasksCheck` is touched only as a `Check`
  registered into `app.build()`'s internal checks dict — neither is known to the dispatcher
  or consumer, guarded by `test_architecture.py`'s existing driver-import checks (which
  already restrict `dispatcher.py`/`consumer.py`, no new guard needed unless the
  architecture step wants a named regression test mirroring invariants 32/34's shape).

Add a clarifying clause to invariant 35 (do not renumber; append, don't replace) noting that
`FailedTasksCheck`'s claim-and-settle of a *pre-existing* task is the same class of
"idempotent, side-effecting claim action" `TaskSource.poll()`'s docstring already sanctions
for `GithubTaskSource`, distinct from "a trigger places the *new* task it produces" — which
still belongs to the dispatcher alone, unchanged.

New ADR `docs/adr/0018-healing-as-a-process.md` (next free number after 0017), recording:
why `Healer` predated the Process idiom and duplicated it; the two resolved decisions in §1
with their rejected alternatives; the process-compilation relocation in §2 and its narrow
scope (extends, doesn't contradict, ADR-0015's "no `build()` signature change" consequence);
and the semantic change in §5.1 (settle note decoupled from heal outcome) as a deliberate,
recorded trade — supersedes the relevant parts of
`docs/superpowers/specs/2026-07-21-self-healing-design.md` (present after the merge) by
reference, without deleting it (additive/historical per `docs/adr/0000-adr-process.md`).

## 8. Test migration (FR-7)

`tests/test_healer.py`'s eight scenarios map as follows (new home in parentheses; each is a
distinct behavioral guarantee, not a 1:1 file rename, because the mechanism split across two
components):

| Old scenario | New coverage |
|---|---|
| `test_done_verdict_files_an_issue_and_settles_to_healed` | **Split.** `FailedTasksCheck` unit test: claiming a failed task settles it to `healed/` with `"queued for healing"` and returns one `Observation` carrying the failure report. `OpenIssueBehavior` unit test: given a task with `data.heal.of` and an `issue.md` artifact, it calls `tracker.open_issue` with `marker=<of>` and returns `done`. An e2e test (below) proves the two compose. |
| `test_request_changes_settles_without_an_issue` | `OpenIssueBehavior` is provably never invoked on this path — covered by an e2e/dispatch test asserting the `heal` workflow routes `request_changes` straight to `end` (a router/workflow test, not a behavior test) plus confirming `tracker.opened == []`. |
| `test_agent_error_settles_to_healed_and_does_not_loop` | Two-hop e2e test: `heal` step's `FakeAgentRunner` raises → task lands in `failed/` (`Consumer._fail`, standard) → next `FailedTasksCheck` tick recognizes `data.heal`, settles to `healed/` with `"heal-failed"`, no second `Observation`. |
| `test_issue_error_settles_to_healed_and_does_not_loop` | Same two-hop shape, `OpenIssueBehavior`'s `IssueError` propagating instead of the agent raising — proves the *same* recursion-guard mechanism covers both failure sites. |
| `test_empty_failed_queue_is_a_noop` | `FailedTasksCheck.evaluate()` on an empty `failed/` → `[]` (direct unit test). |
| `test_lost_claim_race_is_a_noop` | `FailedTasksCheck` unit test with a `claim()`-always-returns-`None` fake queue → `[]`, nothing settled, nothing opened (mirrors the existing test's shape exactly, same fake-queue-subclass technique). |
| `test_second_heal_of_the_same_marker_returns_the_existing_issue` | `OpenIssueBehavior` unit test, called twice with the same `data.heal.of` marker against a shared `MemoryIssueTracker` → one issue (`IssueTracker`'s own idempotency, unchanged, exercised through the new call site). |
| `test_heal_prompt_carries_the_failure_report` | Unchanged in substance — whichever module now owns `heal_prompt`-equivalent prompt composition (still `ClaudeCliBehavior.compose_prompt`, generic, unchanged; the *failure report* content itself — reason/history/original request — is what `FailedTasksCheck.evaluate()`'s `Observation.data` supplies, so this becomes a `FailedTasksCheck` unit test asserting `data["reason"]`/`data["history"]` content, not a standalone prompt-string test). |

Plus **one new end-to-end test** (`FakeClock` + in-memory drivers: `MemoryTaskQueue`,
`FakeAgentRunner`, `MemoryIssueTracker`, `MemoryWorkspace`) driving a failed task through
`ScheduledTrigger` → `SourcePoller` → dispatcher → `heal` → `file-issue` → `end`, proving the
full chain composes — same spirit as today's `test_healer.py` plus whatever
`test_healer_e2e.py`-equivalent exists post-merge (locate and confirm in the architecture
step; this design doc did not find one under that exact name in the file listing read from
`origin/main`, only `tests/test_healer.py` itself — recheck after FR-0's merge).
