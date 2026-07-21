# Self-healing — a healer for the `failed/` queue

Status: draft
Date: 2026-07-21

## Goal

Today a task that lands in `failed/` stays there. `failed` is a **terminal
status** like `end` — a real queue (`FilesystemTaskQueue`), but one that
**nobody consumes** (`ports/queue.py:13`). The failure is recorded (a `reason`
on the failing `HistoryEntry`, a `failed` event carrying the whole task) and
then nothing else happens: an operator has to notice, read the history, and
decide whether the harness itself is at fault.

Self-healing adds a **healer**: when a task fails, an agent reads *why*,
decides whether it is a **fixable harness bug**, and — if so — **opens a GitHub
issue on the harness repo** with a diagnosis and a proposed change. The loop
that produced the failure now produces a lead on its own fix.

The healer is **not** a retry of the original task and **not** a new
`ConsumerBehavior` class. It reuses the machinery already in place: the failure
already emits an event, the agent already runs behind `ClaudeCliBehavior`, the
persona is already data. The only genuinely new parts are a sink that turns a
failure into a heal task, a port for opening an issue, and the healer persona.

### Core thesis: `failed/` stays terminal; the *event* spawns the work

The tempting move is to make a healer **consume** `failed/`. We don't. "Terminal
states are queues nobody consumes" is load-bearing (invariant on `TaskQueue`) —
demoting it would mean the router/dispatcher suddenly have to reason about a
status that has no workflow edges. Instead we mirror phase 4's outward
projection: `SourceReflectorSink` already listens to the `failed` event and
projects it onto the origin issue. The healer listens to the **same event** and
**submits a fresh task** into the inbox. `failed/` is untouched and still
terminal; the failure is merely *observed*, and the observation produces new
work through the front door (`inbox`) like any other task.

This keeps every existing invariant intact:

- The router and dispatcher never learn about `failed` as a step (invariant 4,
  and the `FAILED` reserved-status comment in `models.py`).
- The heal task flows through a **normal workflow** — dispatcher routes it,
  consumer runs it, the agent behavior does the work (invariants 2, 3).
- The persona stays data; there is no branch on the agent's name (invariant 14).
- The outward side effect (opening the issue) is done by the **worker**, not the
  agent, behind a port — exactly as landing opens a PR (invariants 9, 11, 12).

### What's new

- **`SelfHealSink(EventSink)`** — on a `failed` event, compose a heal task and
  `inbox.put()` it, then emit `ingested` (mirrors `SourcePoller`/`SourceReflectorSink`).
- **A `heal` workflow** — `diagnose` → `file_issue` → `end`, run by the existing
  `ClaudeCliBehavior` (a `healer` persona) plus a landing-style `FileIssueBehavior`.
- **`IssueTracker` port** + drivers (`MemoryIssueTracker` fake, `GithubIssueTracker`
  real) — opens an issue on the harness repo, idempotently.
- **The `healer` persona** in `agents/healer.json` — data, not code.

### What's still out of scope

- **Auto-fixing.** The healer opens an *issue*, it does not open a PR against the
  harness. Closing that issue is ordinary harness work (a human, or a later task).
- **Real GitHub in tests.** `GithubIssueTracker` ships; the suite runs on
  `MemoryIssueTracker`, as phase 4 runs on `FakeGithubClient`.
- **Root-cause clustering.** Dedup is per failed task (one failure → at most one
  issue). Grouping many failures under one root cause is a follow-up.
- **Healing the healer.** A heal task that itself fails is *not* healed (see the
  recursion guard) — otherwise a broken healer would spam issues forever.

## The trigger: `SelfHealSink`

The failure event is emitted in two places, both already carrying the full task:

- `Dispatcher._fail` — routing broke (`dispatcher.py:126`).
- `Consumer._fail` — the behavior raised or returned garbage (`consumer.py:125`).

Both emit `("failed", task=<dict>, reason=<str>, queue="failed")`. `SelfHealSink`
is added to the `CompositeEventSink` alongside `ProjectionSink` and
`SourceReflectorSink`:

```python
class SelfHealSink(EventSink):
    def __init__(self, *, inbox, events, clock, ids,
                 heal_workflow, heal_repository, worktree_root):
        ...

    def emit(self, name, **fields):
        if name != "failed":
            return
        raw = fields.get("task")
        if not isinstance(raw, dict):
            return
        failed = Task.from_dict(raw)
        if not self._should_heal(failed):     # recursion + opt-out guard
            return
        heal = self._compose(failed, reason=fields.get("reason", ""))
        self._inbox.put(heal)
        self._events.emit("ingested", task_id=heal.id,
                          queue=TODO_COLUMN, task=heal.to_dict())
```

- **It's a driver** (`drivers/self_heal.py`), so it may hold the `inbox`
  (`TaskQueue`) and a clock / id generator. `dispatcher.py`/`consumer.py` never
  import it — wiring lives in `app.py` (invariant 1). Guarded by
  `test_architecture.py` like every other driver.
- **`inbox.put()` does not emit** (`fs_queue.py:78`); the sink emits `ingested`
  separately with `TODO_COLUMN`, exactly as `SourcePoller.tick` does
  (`source_poller.py:86`). The nested `emit` re-enters `CompositeEventSink`
  harmlessly — an `ingested` event triggers no further `failed`, so there is no
  recursion through the composite.

### The recursion guard — `_should_heal`

Two reasons to *not* spawn a heal task:

1. **The failed task is itself a heal task** (`workflow_template == heal_workflow`).
   Without this, a healer that crashes (a `claude` timeout, a bad verdict) would
   fail into `failed/`, emit `failed`, and spawn *another* healer — an unbounded
   loop. This is the twin of "`DummyBehavior` must return `done` deterministically".
2. **Opt-out.** A task may carry `data.heal = False` to suppress healing (e.g. a
   task known to fail for external reasons). Default (absent) is "heal".

```python
def _should_heal(self, task):
    if task.workflow_template == self._heal_workflow:
        return False
    if task.data.get("heal") is False:
        return False
    return True
```

### Dedup — one failure, at most one heal task

The `failed` event fires **once per failure transition**, so the normal path
creates exactly one heal task. `recover()` re-queues in-flight tasks but does not
re-emit `failed`, so a restart does not duplicate. To make the heal task's
identity legible we stamp `dedup_key = f"heal:{failed.id}"` — a task that fails,
gets restarted (invariant 23, `TaskControl.restart`), and fails *again* will
produce a second heal task, which is correct: it is a second, distinct failure
worth a second look. (Clustering those into one issue is out of scope; the
`IssueTracker` idempotency below stops the *duplicate* issue, not the duplicate
task.)

### What the heal task carries

`SelfHealSink._compose` builds a normal `Task`:

```python
Task(
    id=self._ids.new_task_id(),
    workflow_template=self._heal_workflow,          # "heal"
    created=self._clock.now(),
    repository=self._heal_repository,               # the harness repo NAME
    worktree=f"{self._worktree_root}/{id}",
    dedup_key=f"heal:{failed.id}",
    data={
        "request": _failure_report(failed, reason),  # human-readable, drives the prompt
        "heal_of": failed.id,                        # for the issue marker / dedup
        "source": failed.data.get("source"),         # so the issue can link the origin
    },
)
```

- **`repository` is a name** (invariant 15), resolved by `RepositoryRegistry`.
  The healer needs to read harness source to propose a concrete change, so the
  heal repository is the **harness's own repo**, registered in `repos.json` under
  a configured name (default `"harness"`). The worktree path is derived by the
  harness (`<worktrees_root>/<task_id>`), same as any task.
- **`data.request`** is a formatted failure report: the failed task's id,
  workflow, the failing step, the `reason`, and the `summary`s from its consumer
  history. `compose_prompt` already surfaces `data.request` as "Task: …"
  (`behaviors/agent.py:99`) — so **no change to `compose_prompt` or
  `ClaudeCliBehavior`** is needed. The failure detail reaches the agent purely as
  data.

## The `heal` workflow

A two-step state machine, shipped as a workflow JSON (written by `harness init`):

```
diagnose --done--> file_issue --done--> end
diagnose --request_changes--> end
```

| Step | Behavior | Outcome → next |
|---|---|---|
| `diagnose` | `ClaudeCliBehavior` (persona `healer`) | `done` → `file_issue`; `request_changes` → `end` |
| `file_issue` | `FileIssueBehavior` (worker opens the issue) | `done` → `end` |

- **Dual outcome on `diagnose`** reuses the reviewer's pattern (`reviewer` may
  return `done`+`request_changes`). Here the meaning is: `done` = "I have a
  concrete, fixable proposal — file it"; `request_changes` = "nothing actionable
  for the harness — stop cleanly at `end`". Routing this through two real edges
  keeps the decision in workflow data, not in code (invariants 2, 4).
- **`file_issue` is a step, not magic** (invariant 12, the landing precedent):
  it reads the healer's draft artifact via `ArtifactView`, opens the issue via
  `IssueTracker`, and returns `done`. It can fail into `failed/` like any step —
  and that failure is **not** re-healed (the recursion guard), it just surfaces.

### The `healer` persona (`agents/healer.json`)

Data only (invariant 14). The prompt instructs the agent to:

1. Read `data.request` (the failure report) and the harness source in its cwd.
2. Decide whether the failure points at a **fixable harness bug** (a driver
   contract, a wiring gap, a missing edge) versus an external/expected failure
   (a flaky network, a task that was simply wrong).
3. If fixable: write the proposed issue to its artifact
   (`.artifacts/<id>/diagnose-NN.md`) — a title line, a diagnosis, and a
   concrete proposed change — and finish with `{"outcome": "done", "summary": "<issue title>"}`.
4. If not: finish with `{"outcome": "request_changes", "summary": "<why nothing to do>"}`.

`allowed_outcomes = (done, request_changes)`. The agent **writes the artifact and
returns a verdict; it does not open the issue** — the worker does (invariant 9).

### `FileIssueBehavior`

Mirrors `LandingBehavior` (`behaviors/landing.py`) one-to-one, swapping `Forge`
for `IssueTracker`:

```python
class FileIssueBehavior(ConsumerBehavior):
    def __init__(self, *, clock, artifacts: ArtifactView, tracker: IssueTracker,
                 repo: str, labels=("harness:self-heal",)):
        ...

    async def run(self, task):
        title, body = self._draft(task)         # from the latest diagnose artifact
        issue = self._tracker.open_issue(
            self._repo, title=title, body=body, labels=self._labels,
            marker=task.data.get("heal_of", task.id),
        )
        return BehaviorResult(Outcome.DONE, f"opened issue {issue.url}")
```

- **Reads only `ArtifactView`** (invariant 11 — behavior may touch the artifact
  read side; `api/` may too, orchestration may not). It picks the latest
  `diagnose` artifact: title = the summary (first `# ` line of the artifact, else
  `task.data.request`'s first line), body = the artifact content plus a footer
  linking `data.source` if present.
- **No worktree, no commit.** Unlike landing, the healer's output is not code in
  the harness worktree — it's an issue. `FileIssueBehavior` therefore does not
  `attach`/`commit`; it only reads the artifact and calls the port. (The
  `diagnose` step's own worktree still exists — the agent read harness source
  there — but the issue is the deliverable, not a branch.)

## The `IssueTracker` port

`Forge` (phase 2) opens **pull requests**; `TaskSource.finish` (phase 4)
**projects state** onto an existing issue by relabeling. Neither *creates a fresh
advisory issue*. That is a third, distinct verb, so it gets its own port:

```python
@dataclass(frozen=True)
class IssueRef:
    number: int
    url: str

class IssueError(Exception): ...

class IssueTracker(ABC):
    @abstractmethod
    def open_issue(self, repo: str, *, title: str, body: str,
                   labels: tuple[str, ...], marker: str) -> IssueRef:
        """Open an issue on `repo`. Idempotent per `marker`: if an open issue
        already carries the marker, return it instead of creating a second."""
```

- **Idempotency by marker** mirrors `GithubForge` matching on `head=owner:branch`
  (the "Landing is idempotent" gotcha). The marker is the failed task id; the
  driver embeds it as an HTML comment (`<!-- harness-heal:<id> -->`) in the body
  and searches open issues for it before creating. A re-run after a crash won't
  open a second issue.
- **Failure raises `IssueError`** → `file_issue` fails into `failed/` (and is not
  re-healed). Symmetric to `ForgeError` (the "A failed PR fails the task" gotcha).

### Drivers

- **`MemoryIssueTracker`** — issues in a list, `open_issue` dedups on the marker.
  For unit/e2e/smoke. No network.
- **`GithubIssueTracker`** — reuses `GithubClient`. Two methods are **added** to
  `GithubClient` (ABC + `FakeGithubClient` + `HttpGithubClient`), no new
  production dependency (stdlib `urllib`, invariant from phase 4):
  - `create_issue(repo, *, title, body, labels) -> Issue` — `POST /repos/{repo}/issues`.
  - `search_issue_by_marker(repo, marker) -> Issue | None` — reuse
    `list_issues(repo, label="harness:self-heal")` and match the marker in the
    body (avoids depending on the Search API).

`GithubIssueTracker.open_issue` = `search_issue_by_marker` → return if found,
else `create_issue` with the marker appended to the body.

## Model, wiring, CLI

- **`Task` does not change.** `data.heal_of` / `data.heal` are conventions inside
  the existing `data`, like `data.source` in phase 4.
- **`models.py`**: no change. `FAILED` stays a reserved terminal status.
- **`app.py`**: `build(...)` gains `issue_tracker: IssueTracker | None` and
  `heal: HealConfig | None` (workflow name, repository name, labels). When `heal`
  is set it:
  1. adds `SelfHealSink(inbox=…, events=composite, clock=…, ids=…, heal_workflow=…,
     heal_repository=…, worktree_root=…)` to the composite (constructed after the
     composite exists, then registered — the sink emits back into the same
     composite, same as the poller);
  2. maps the `file_issue` step to `FileIssueBehavior` in `behavior_for` (next to
     the existing `landing_step` special-case);
  3. the `diagnose` step is an ordinary catalog step → `ClaudeCliBehavior`.
  Default `heal=None` and `issue_tracker=None` → **fully backward compatible**:
  no heal workflow served, no sink, `submit`/phase-4 flow unchanged.
- **`cli.py`**: `harness init` writes `workflows/heal.json` and `agents/healer.json`
  and registers the harness repo name in `repos.json`. `harness run` serves the
  `heal` workflow when configured and wires `GithubIssueTracker(HttpGithubClient(...))`
  when `GITHUB_TOKEN` is present (else `MemoryIssueTracker`, offline).
- **`Harness.run()`**: no new loop. The heal task rides the existing dispatcher /
  consumer loops for the `diagnose` / `file_issue` steps — the reason a sink was
  chosen over a bespoke healer loop.

## Interaction with phase 4 (the origin issue)

A failed task born from a GitHub issue already gets `finish(ok=False)` → the
`harness:failed` label, via `SourceReflectorSink` (unchanged). Self-healing is
**additive and independent**: the same `failed` event also produces a heal task.
The two sinks don't know about each other; `CompositeEventSink` isolates a
failure in either. The heal issue links back to the origin via `data.source` in
its body, but the healer's repository is always the **harness** repo, not the
origin's.

## Error states

| Situation | Detection | Result |
|---|---|---|
| Heal task itself fails (`claude` timeout, bad verdict) | `_should_heal` sees `workflow_template == heal` | **not** re-healed; lands in `failed/`, surfaces to the operator |
| `SelfHealSink.emit` raises (bad task dict, inbox write) | `CompositeEventSink` isolates it | traceback to stderr; the orchestration loop and the other sinks go on |
| `diagnose` finds nothing actionable | agent returns `request_changes` | routed straight to `end`; no issue opened |
| `open_issue` raises (`IssueError`, no token, API error) | exception in `FileIssueBehavior` | `file_issue` fails into `failed/`; not re-healed |
| Duplicate issue for the same failure (crash re-run) | `IssueTracker` marker search | existing issue returned, no second issue |

## UI

The board **does not change**. A heal task is an ordinary task on its own
`heal` workflow lane (`diagnose`, `file_issue`); its `data.heal_of` is visible in
the task detail. A "this task spawned heal-task X" backlink on the failed card is
an optional follow-up, not part of this feature.

## Code structure (additions)

```
src/harness/
  ports/
    issues.py            # IssueTracker, IssueRef, IssueError
  behaviors/
    issue.py             # FileIssueBehavior (landing-style, uses IssueTracker)
  drivers/
    self_heal.py         # SelfHealSink(EventSink)
    memory.py            # + MemoryIssueTracker
    github_client.py     # + create_issue / search_issue_by_marker (ABC, Fake, Http)
    github_issues.py     # GithubIssueTracker(IssueTracker)
  app.py                 # wiring: SelfHealSink + FileIssueBehavior + HealConfig
  cli.py                 # init writes heal.json/healer.json; run wires the tracker
workflows/heal.json      # diagnose -> file_issue -> end (shipped by `harness init`)
agents/healer.json       # the persona (data)
```

## Invariants — new

Continuing the numbered list in `CLAUDE.md` (…23):

24. **A failure is *observed*, never *consumed*.** `failed/` stays terminal
    (invariant on `TaskQueue`); healing is triggered by the `failed` **event**
    via `SelfHealSink`, which submits a fresh heal task through the inbox. The
    router/dispatcher never learn about `failed` as a step.
25. **The healer heals everything but itself.** `SelfHealSink` never spawns a heal
    task for a task on the heal workflow (`_should_heal`), so a broken healer
    cannot loop. `data.heal = False` opts an individual task out.
26. **The healer's deliverable is an issue, opened by the worker.** The `diagnose`
    agent only writes a draft artifact and returns a verdict; `FileIssueBehavior`
    opens the issue via `IssueTracker` (invariant 9). `IssueTracker` is a third
    port distinct from `Forge` (opens PRs) and `TaskSource.finish` (relabels).
27. **`IssueTracker`/`SelfHealSink` are unknown to the dispatcher and consumer.**
    Only the behavior / sink / wiring touch them; `app.py` wires them. Guarded by
    `test_architecture.py`.

## Completion check

Self-healing is done when:

1. A task fails (routing error or a raising behavior). A `failed` event fires; a
   heal task appears in the inbox with `workflow_template="heal"`,
   `repository=<harness>`, `data.heal_of=<failed id>`, and `data.request` carrying
   the failure report.
2. The heal task itself failing produces **no** second heal task (the recursion
   guard) — asserted directly.
3. `diagnose` returning `request_changes` routes to `end` with no issue opened;
   returning `done` routes to `file_issue`.
4. `file_issue` opens an issue on the harness repo via `MemoryIssueTracker`; the
   body carries the marker and links `data.source` when present.
5. A second run for the same failed task returns the **existing** issue (marker
   dedup), not a second one.
6. A normal (`submit`/GitHub) task with no failure passes through with **not a
   single** `IssueTracker` call, and `build()` with `heal=None` behaves exactly as
   before (backward compatibility).
7. E2E on in-memory drivers (Memory queue/workspace/artifacts, `FakeClock`,
   `FakeAgentRunner`, `MemoryIssueTracker`): induced failure → heal task →
   diagnose → file_issue → issue.
8. Architecture tests: `dispatcher`/`consumer` don't import `ports/issues` or
   `drivers/self_heal`; `behaviors/issue.py` imports only ports; nobody but
   `app.py`/`cli.py` wires the drivers.
9. `GithubIssueTracker` exists and adds no production dependency (stdlib only).
