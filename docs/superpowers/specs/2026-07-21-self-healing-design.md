# Self-healing — a healer agent assigned to the `failed/` queue

Status: draft
Date: 2026-07-21

## Goal

Today a task that lands in `failed/` stays there. `failed` is a **terminal
status** — a real queue (`FilesystemTaskQueue`), but one that **nobody consumes**
(`ports/queue.py:13`). The failure is recorded (a `reason` on the failing
`HistoryEntry`, a `failed` event carrying the whole task) and then nothing else
happens: an operator has to notice, read the history, and decide whether the
harness itself is at fault.

Self-healing **assigns an agent — a `healer` — to the `failed` queue**. Queues
and agents are not rigidly one-to-one; the healer is simply the agent we point at
`failed/`, the same way every step queue already has an agent behind it. The
healer reads a failed task, decides whether it points at a **fixable harness
bug**, and — if so — **opens a GitHub issue on the harness repo** with a
diagnosis and a proposed change. The queue that used to be a dead end becomes a
work queue that drains.

### Core thesis: `failed/` gets a reader; `healed/` is the new terminal

The model is deliberately the plainest one: **a queue, an agent on it.** The
healer *consumes* `failed/` — reads a task, acts, and moves it on to a new
terminal `healed/` queue. This is the one change to "terminal states are queues
nobody consumes": `failed/` is now consumed by exactly one reader (the healer),
and `healed/` takes over the role of the never-consumed terminal.

Choosing a **consuming reader** over "spawn a new heal task from the failed
event" buys three things:

1. **No recursion to guard.** The healer produces *issues*, not tasks. Nothing it
   does can re-enter `failed/`, so a broken healer cannot loop. (A spawn-a-task
   design has to guard against the heal task itself failing and spawning another.)
2. **The failed task is the input, in place.** No second task, no snapshot copied
   into `data`, no correlation to maintain — the healer reads exactly the task
   that failed.
3. **`failed/` drains monotonically.** Every task the healer claims leaves
   `failed/` for `healed/` exactly once (an atomic move, the same primitive
   `claim` uses). A task can't be healed twice because it's no longer there.

Everything else in the harness is untouched: the router and dispatcher never
learn about `failed`/`healed` as steps (invariant 4); the healer doesn't ride the
dispatcher at all, so the consumer/dispatcher separation (invariants 2, 3) is not
even in play; the persona stays data (invariant 14); the outward side effect
(opening the issue) is done by the **worker loop**, not the LLM (invariant 9).

### What's new

- **A `Healer` core loop** (`healer.py`) — assigned to the `failed` queue,
  alongside the dispatcher / consumer / source loops in `Harness.run()`. Knows
  only ports (`TaskQueue`, `AgentRunner`, `IssueTracker`, `EventSink`, `Clock`),
  never a driver — the same shape as `SourcePoller`.
- **A `healed/` terminal queue** — where healed tasks come to rest.
- **`IssueTracker` port** + drivers (`MemoryIssueTracker` fake, `GithubIssueTracker`
  real) — opens an issue on the harness repo, idempotently.
- **The `healer` persona** in `agents/healer.json` — data, not code.

### What's still out of scope

- **Auto-fixing.** The healer opens an *issue*, it does not open a PR against the
  harness. Closing that issue is ordinary harness work (a human, or a later task).
- **Reading the failed task's worktree / artifacts.** v1 diagnoses from the
  **failure report** (reason + history). Attaching the origin worktree, or the
  harness source, to let the healer cite exact lines is a clean follow-up.
- **Real GitHub in tests.** `GithubIssueTracker` ships; the suite runs on
  `MemoryIssueTracker`, as phase 4 runs on `FakeGithubClient`.
- **Root-cause clustering.** One failed task → at most one issue. Grouping many
  failures under one root cause is a follow-up (`IssueTracker` dedups the
  *duplicate* issue, not the duplicate failure).
- **Retry.** The healer diagnoses; it does not re-run the original task. Retrying
  is the operator's call via `TaskControl.restart` (invariant 23).

## The `Healer` loop

A sibling of `SourcePoller` — a core loop that owns one queue:

```python
class Healer:
    def __init__(self, *, failed: TaskQueue, healed: TaskQueue,
                 runner: AgentRunner, spec: AgentSpec, tracker: IssueTracker,
                 repo: str, scratch_root: Path, events: EventSink, clock: Clock,
                 labels=("harness:self-heal",), timeout=1800.0):
        ...

    async def tick(self) -> bool:
        selected = self._strategy.select(self._failed.list())
        if selected is None:
            return False
        task = self._failed.claim(selected, new_lock_id())   # atomic; lost race → None
        if task is None:
            return False

        scratch = self._scratch_root / task.id               # a plain dir, NOT a git worktree
        prompt = _heal_prompt(task, spec=self._spec)          # from reason + history
        run = await self._runner.run(prompt=prompt, spec=self._spec,
                                     cwd=scratch, timeout=self._timeout)

        note = self._act_on(task, run, scratch)               # file the issue, or not
        self._settle(task, note)                              # -> healed/, history + event
        return True
```

- **`claim()` is the dedup.** The atomic `rename` into `.processing/` means two
  healer ticks (or a restart) never process the same failed task twice — exactly
  the guarantee the step queues already rely on. Losing the race returns `None`
  and the tick moves on (the "Losing the race for `claim()` is not an error"
  gotcha).
- **The agent drafts; the loop files.** `_act_on` reads the agent's verdict:
  `done` → the agent wrote its proposed issue to `scratch/issue.md`; the loop
  reads it and calls `tracker.open_issue(...)`. Any other outcome (or "nothing
  actionable") → no issue. Either way `_settle` moves the task to `healed/`. The
  **loop is the worker** that performs the outward side effect, never the LLM
  (invariant 9).
- **Failure never returns to `failed/`.** If the agent errors, times out, or
  `open_issue` raises, `_settle` still moves the task to `healed/` — with a
  `heal-failed` note in its history and an event carrying the reason. `failed/`
  drains monotonically; the fault surfaces on `healed/` and in the event stream,
  it does not loop. (Optionally a distinct `heal_failed/` terminal instead of a
  note — a plan decision; default is one `healed/` terminal with the note.)

### The scratch directory (no worktree)

The healer needs a `cwd` for the agent but **no git worktree** — its deliverable
is an issue, not a commit. It runs the agent in `scratch_root/<task_id>/`, a plain
directory under the harness root. The agent writes `issue.md` there; the loop
reads it as the issue body. `Workspace` / `RepositoryRegistry` are not involved,
so the healer needs no `repos.json` entry. (When a later version wants the healer
to cite harness source, that is where a real `Workspace.attach` would slot in.)

### The failure report → prompt

`_heal_prompt` builds the instruction from the task alone — no code change to the
existing `compose_prompt`, which is worktree/artifact-shaped. It states: the
failed task's id and origin workflow, the failing step (`task.status`), the
`reason` from the failing history entry, and the consumer-history summaries; then
the persona's standing instruction to diagnose and, if fixable, write
`scratch/issue.md` and end with the verdict block.

### The `healer` persona (`agents/healer.json`)

Data only (invariant 14), loaded by the existing `AgentCatalog` at wiring time and
handed to the loop as a single `AgentSpec`. The prompt instructs the agent to:

1. Read the failure report in the prompt.
2. Decide whether it points at a **fixable harness bug** (a driver contract, a
   wiring gap, a missing workflow edge) versus an external/expected failure (a
   flaky network, a task that was simply wrong).
3. If fixable: write the proposed issue to `issue.md` in its cwd — a `# title`
   line, a diagnosis, and a concrete proposed change — and finish with
   `{"outcome": "done", "summary": "<issue title>"}`.
4. If not: finish with `{"outcome": "request_changes", "summary": "<why nothing to do>"}`.

`allowed_outcomes = (done, request_changes)`. The verdict is read by the loop, not
routed by the dispatcher — `done` means "I filed a proposal", `request_changes`
means "nothing for the harness"; both settle to `healed/`.

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
  and searches open issues for it before creating. A re-run (a crash between
  `open_issue` and `_settle`, before the task left `failed/`) won't open a second
  issue.
- **Failure raises `IssueError`** → the loop settles the task to `healed/` with a
  `heal-failed` note. Symmetric to `ForgeError` (the "A failed PR fails the task"
  gotcha), minus the re-queue.

### Drivers

- **`MemoryIssueTracker`** — issues in a list, `open_issue` dedups on the marker.
  For unit/e2e/smoke. No network.
- **`GithubIssueTracker`** — reuses `GithubClient`. Two methods are **added** to
  `GithubClient` (ABC + `FakeGithubClient` + `HttpGithubClient`), no new
  production dependency (stdlib `urllib`, per phase 4):
  - `create_issue(repo, *, title, body, labels) -> Issue` — `POST /repos/{repo}/issues`.
  - `search_issue_by_marker(repo, marker) -> Issue | None` — reuse
    `list_issues(repo, label="harness:self-heal")` and match the marker in the
    body (avoids depending on the Search API).

`GithubIssueTracker.open_issue` = `search_issue_by_marker` → return if found,
else `create_issue` with the marker appended to the body.

## Model, wiring, CLI

- **`Task` does not change.** No new fields; the healer works with the failed task
  as-is. `FAILED` stays a reserved status; a new `HEALED = "healed"` reserved
  status marks a task that has come to rest on `healed/`.
- **`app.py`**: `build(...)` gains `issue_tracker: IssueTracker | None` and
  `heal: HealConfig | None` (dataclass: `spec_name="healer"`, `repository`,
  `labels`, `heal_failed_queue: bool = False`). When both are set it builds the
  `healed/` queue, resolves the healer spec via the catalog, and constructs a
  `Healer` bound to `failed`/`healed`. Default `heal=None` / `issue_tracker=None`
  → **fully backward compatible**: no healer, `failed/` stays a dead-end terminal,
  `submit`/phase-4 flow unchanged.
- **`Harness.run()`**: adds a heal loop to the `asyncio.gather` (a tick = claim +
  run + settle, else sleep `poll_interval`), exactly as the source loop was added.
  `recover()` already returns in-flight tasks to `failed/` on restart — the
  healer's `.processing/` is recovered like any queue's.
- **`cli.py`**: `harness init` writes `agents/healer.json`. `harness run` wires
  `GithubIssueTracker(HttpGithubClient(...))` when `GITHUB_TOKEN` is present (else
  `MemoryIssueTracker`, offline) and enables the healer against the configured
  harness repo. Without the flag it runs exactly as before.

## Interaction with phase 4 (the origin issue)

A failed task born from a GitHub issue already gets `finish(ok=False)` → the
`harness:failed` label, via `SourceReflectorSink` (unchanged). Self-healing is
**additive and independent**: the healer reads the same task from `failed/` and
files a *separate* issue on the **harness** repo (linking the origin via
`task.data.source` in the body). When the task later moves `failed → healed`, no
new outward projection fires — `SourceReflectorSink` only maps `dispatched /
finished / failed`, and the healer's settle emits a distinct `healed` event that
the reflector ignores. The origin issue keeps its `harness:failed` label; the
harness-repo issue is the healer's separate deliverable.

## Error states

| Situation | Detection | Result |
|---|---|---|
| Two ticks / a restart hit the same failed task | `claim()` atomic rename | one wins, the other gets `None` and moves on — no double heal |
| Healer agent errors / times out | exception around `runner.run` | task settles to `healed/` with a `heal-failed` note + event; **not** re-queued to `failed/` |
| `open_issue` raises (`IssueError`, no token, API error) | exception in `_act_on` | task settles to `healed/` with a `heal-failed` note; no loop |
| Agent finds nothing actionable | verdict `request_changes` | task settles to `healed/`, no issue opened |
| Duplicate issue for the same failure (crash before settle) | `IssueTracker` marker search | existing issue returned, no second issue |
| Healer disabled (`heal=None`) | no loop wired | `failed/` behaves exactly as today (dead-end terminal) |

## UI

The board gains one lane: `healed/` alongside `done/` and `failed/`. `failed/`
now visibly drains as the healer works; a healed card shows in its history whether
an issue was filed (the `_settle` note) — including the issue URL. The
board/projection change is additive (a new terminal column), like `done`/`failed`
already are.

## Code structure (additions)

```
src/harness/
  ports/
    issues.py            # IssueTracker, IssueRef, IssueError
  healer.py              # Healer core loop (knows only ports), + _heal_prompt
  drivers/
    memory.py            # + MemoryIssueTracker
    github_client.py     # + create_issue / search_issue_by_marker (ABC, Fake, Http)
    github_issues.py     # GithubIssueTracker(IssueTracker)
  app.py                 # wiring: healed/ queue + Healer loop + HealConfig
  cli.py                 # init writes healer.json; run wires the tracker + healer
agents/healer.json       # the persona (data), shipped by `harness init`
```

## Invariants — new / refined

Continuing the numbered list in `CLAUDE.md` (…23):

24. **`failed/` has exactly one reader: the healer; `healed/` is the new
    never-consumed terminal.** This refines "terminal states are queues nobody
    consumes" — `done`, `end` and `healed` are terminal; `failed` is drained by
    the `Healer` loop and by nothing else. The router and dispatcher never learn
    about `failed`/`healed` as steps.
25. **The healer produces an issue, never a task, and never writes to `failed/`.**
    So no failure can be healed twice and nothing can loop — a heal attempt claims
    a task out of `failed/` exactly once and settles it to `healed/`, success or
    failure. The recursion problem of a spawn-a-task design does not exist.
26. **The healer's deliverable is opened by the worker loop, not the LLM.** The
    agent (persona as data) only writes a draft and returns a verdict; the
    `Healer` loop reads the draft and calls `IssueTracker`. `IssueTracker` is a
    third port distinct from `Forge` (opens PRs) and `TaskSource.finish`
    (relabels).
27. **`IssueTracker` and the `Healer` loop are unknown to the dispatcher and
    consumer.** The healer is a core loop that knows only ports (like
    `SourcePoller`); wiring lives in `app.py`. Guarded by `test_architecture.py`.

## Completion check

Self-healing is done when:

1. A task in `failed/` is claimed by the `Healer` loop, run through the healer
   agent, and moved to `healed/` — `failed/` no longer contains it.
2. Agent verdict `done` → an issue is opened on the harness repo (via
   `MemoryIssueTracker`) with the marker and a `data.source` backlink when
   present; verdict `request_changes` → no issue, still settled to `healed/`.
3. A second heal attempt for the same task id returns the **existing** issue
   (marker dedup), not a second one.
4. The healer agent raising, or `open_issue` raising, still settles the task to
   `healed/` (with a `heal-failed` note) and never returns it to `failed/` —
   asserted directly (no loop).
5. Two concurrent ticks over the same `failed/` task heal it **once** (`claim`
   atomicity).
6. With `heal=None`, `build()` wires no healer and `failed/` behaves exactly as
   before (backward compatibility) — no `IssueTracker` call for any task.
7. E2E on in-memory drivers (Memory queue/artifacts, `FakeClock`,
   `FakeAgentRunner` scripted for the healer, `MemoryIssueTracker`): induced
   failure → task in `failed/` → healer → issue → task in `healed/`.
8. Architecture tests: `dispatcher`/`consumer` don't import `ports/issues` or
   `healer.py`; `Healer` imports only ports/models; nobody but `app.py`/`cli.py`
   wires the drivers.
9. `GithubIssueTracker` exists and adds no production dependency (stdlib only).
