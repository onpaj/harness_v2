# Phase 4 — task source connector (GitHub)

Status: draft
Date: 2026-07-20

## Goal

Tasks stop being created only by hand (`harness submit` writes JSON into the inbox).
In phase 4 a task **flows in from the real world of work management** — from GitHub
Issues — and its status is **projected back** into that world. That whole outer
world sits behind **a single port**, `TaskSource`, with three verbs:

- **`poll()`** — bring in new, not-yet-consumed tasks.
- **`report_progress(task, progress)`** — project the in-flight status outward.
- **`finish(task, result)`** — project the terminal status (success / failure).

GitHub is **one implementation** of that port. A filesystem "drop-folder" is another.
Jira, Linear, and the like are more — **swap the driver, never its surroundings**.
How the status is rendered outward is the driver's business: the GitHub adapter does
it **by changing labels** on the issue.

Phase 4 is still a POC. The real agent stays a dummy; the actual GitHub calls are a
thin HTTP driver behind `GithubClient` — the tests run against an in-memory fake.

### What's new in phase 4

- The `TaskSource` port and the `SourcePoller` loop that fills the inbox from it —
  a second producer for the same queue alongside `harness submit`.
- The task's origin travels **with the task** in `data.source` (`{kind, repo, issue, url}`),
  durably. Any later projection outward (a label, a "Closes #42") reads it from there.
- `SourceReflectorSink` — an `EventSink` that translates the stream of harness events
  into `report_progress`/`finish` calls. It mirrors `ProjectionSink` (the board), only
  it points outward instead of at the UI.
- GitHub drivers: `GithubTaskSource` + `GithubClient` (ABC) with `FakeGithubClient`
  (tests) and `HttpGithubClient` (real, stdlib `urllib`, no new dependency).

### What's still out of scope

- **A real agent.** The behavior is a dummy, just like in phase 2/3.
- **Real GitHub in tests.** `HttpGithubClient` ships, but the unit/e2e suite runs on
  `FakeGithubClient`. A live run is a swap of the client.
- **Multiple processes, rate-limit backoff, lease TTL, webhooks.** A poll is a plain tick.

## Core thesis (ARD4): the outer world is one port

The harness doesn't know GitHub. It knows `TaskSource` with three verbs. Everything
GitHub-specific — finding issues, label names, HTTP — lives in the driver. Three
consequences that phase 4 protects:

1. **One abstract shape, N implementations.** GitHub, filesystem, Jira are siblings
   behind the same port. Adding a source = adding a driver, not reaching into the core.
2. **The origin travels with the task.** The "task ↔ issue" correlation lives in
   `task.data.source`, not in a side table. It's durable (the task is persisted) and the
   projection outward reads it without shared state.
3. **The projection outward is only a projection.** `report_progress`/`finish` change
   nothing in the harness and decide nothing — their only side effect is outward. A
   GitHub failure must not stall the loop (`CompositeEventSink` isolates it).

## The `TaskSource` port

```python
@dataclass(frozen=True)
class Progress:
    step: str            # the step the task has just entered
    summary: str = ""    # what happened (optional, from history)

@dataclass(frozen=True)
class FinishResult:
    ok: bool
    pr_url: str | None = None
    summary: str = ""

class TaskSource(ABC):
    kind: str            # "github" / "memory" / "fs" — key for routing the projection

    def poll(self) -> list[Task]: ...
    def report_progress(self, task: Task, progress: Progress) -> None: ...
    def finish(self, task: Task, result: FinishResult) -> None: ...
```

The harness's internal vocabulary `(status, last_outcome, queue)` **does not leak**
through the port. The reflector maps it to `Progress`/`FinishResult`; the adapter maps
that to a label. Two mapping layers, each in its own layer:

- **harness event → `Progress`/`FinishResult`** — in the reflector, with no knowledge of GitHub.
- **`Progress`/`FinishResult` → label** — in the GitHub adapter, the only place that knows GitHub.

## Two verb-adapter tables

| Verb | GitHub adapter | Filesystem adapter |
|---|---|---|
| `poll()` | `list_issues(repo, label=select)`, for each issue **flip the label** `todo→queued` (claim) and assemble a `Task` with `data.source` | read the new specs in the drop dir, atomically move to `.claimed/`, assemble a `Task` |
| `report_progress` | remove the current managed label, add `step_labels[step]` (unknown step → no label, coarse) | write `step`/`summary` into JSON |
| `finish` | success → `pr-open`; failure → `failed` | move JSON to `done/` / `failed/` |

**Label-as-claim.** Flipping the label in `poll()` is GitHub's twin of the atomic
`rename` in `fs_queue.claim()` — the next poll won't return an issue that carries the
claim label. This gives "at most once" ingestion without a side ledger.

**Dual-write.** `poll()` flips the label *before* the task departs into the inbox. A
crash in between → a lost task, but **visibly** (the issue hangs on `queued` with no
PR). We choose that over duplication (two PRs). The reconcile loop (below) catches up
the lost ones.

## Where those three verbs are called

- `poll()` → **`SourcePoller`** — a new loop in `Harness.run()` alongside the
  dispatcher/consumers. `for t in source.poll(): inbox.put(t)`. The core stays
  GitHub-blind; `SourcePoller` knows only ports (`TaskSource`, `TaskQueue`, `EventSink`).
- `report_progress`/`finish` → **`SourceReflectorSink(EventSink)`**, added to the
  `CompositeEventSink`. It maps:
  - `dispatched` (task entered a step) → `report_progress(task, Progress(step=queue, …))`
  - `finished` (task reached `end`) → `finish(task, FinishResult(ok=True))`
  - `failed` → `finish(task, FinishResult(ok=False, summary=reason))`

The reflector holds a **list** of sources and routes by `task.data.source.kind`; an
adapter silently ignores a foreign task (a different `kind`, or no `source`). Tasks
from `harness submit` thus pass through with no projection outward.

Net: `TaskSource` is touched only by `SourcePoller` (core) and `SourceReflectorSink`
(driver), both wired in `app.py`. `dispatcher.py`/`consumer.py` don't import the port —
`test_architecture.py` guards it.

## `report_progress` is idempotent → reconcile comes for free

Setting a label that's already set is a no-op. So a simple **reconcile loop** can, on
restart, re-derive the target label from the task's *current* status and call
`report_progress` — missed events catch themselves up, exactly as the board re-hydrates.
Phase 4 **specifies the reconcile loop as an optional step of the plan**; the projection
core is purely event-driven, reconcile is a safety net.

## Relationship to the `Forge` port

`Forge` (phase 2) **creates** PRs. `TaskSource.finish` **projects** the terminal status
onto the *issue*. They stay **two ports**, even though a GitHub implementation may share
one HTTP client between them:

- A source without integrated VCS (filesystem, Jira-without-GitHub) implements `finish`
  meaningfully (moving JSON) — without `Forge`.
- The issue↔PR link ("Closes #42") is done by the **GitHub `Forge` driver** in the PR
  body, because it has `task.data.source.issue`. `finish` therefore needs no URL for the
  label — coarse state (`pr-open`) is enough. `landing.py:50` already returns
  `opened PR {url}` into the summary, in case the reflector wanted the URL in a comment;
  for the label it doesn't need it.

Whether phase 4 also ships a live GitHub `Forge` is a plan decision (the "real GitHub"
task). Default: the connector (source + projection) is the phase's core; a live `Forge`
is a clean follow-up in the same spirit as phase 2 anticipated.

## GitHub drivers

```python
@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    url: str
    labels: tuple[str, ...]

class GithubClient(ABC):
    def list_issues(self, repo: str, *, label: str) -> list[Issue]: ...
    def add_label(self, repo: str, number: int, label: str) -> None: ...
    def remove_label(self, repo: str, number: int, label: str) -> None: ...  # idempotent (404 → nothing)
```

- **`FakeGithubClient`** — issues in a dict, `list_issues` filters by label,
  add/remove mutate. For unit/e2e and smoke.
- **`HttpGithubClient`** — real, stdlib `urllib.request` against `api.github.com`,
  token from `GITHUB_TOKEN`. `GET /repos/{repo}/issues?labels=&state=open`,
  `POST /repos/{repo}/issues/{n}/labels`, `DELETE …/labels/{label}`. No new production
  dependency.

`GithubTaskSource(TaskSource)`:
- `kind = "github"`.
- Config: `client, clock, repo, workflow, repository, worktree_root,
  select_label="harness:todo", claimed_label="harness:queued",
  pr_label="harness:pr-open", failed_label="harness:failed", step_labels: dict`.
- `poll()`: for each issue with `select_label` → remove `select`, add `claimed`,
  `Task(id=new_task_id(), workflow_template=workflow, created=clock.now(),
  repository=repository, worktree=f"{worktree_root}/{id}",
  data={"title","body","source":{kind,repo,issue,url}})`.
- `_set_state(number, target)`: from the **known managed set**
  `{claimed, pr, failed, *step_labels.values()}` remove everything but `target`, add
  `target`. The managed set is known → exactly one state label at a time, idempotently.

## Model, wiring, CLI

- **`Task` doesn't change.** `data.source` is just a convention within the already
  existing `data`.
- **`app.py`**: `build(...)` takes `sources: list[TaskSource] | None`; it builds a
  `SourcePoller` per source and adds `SourceReflectorSink(sources)` to the composite.
  Default `sources=[]` (backward compatible — `submit` keeps working).
- **`cli.py`**: `run` wires `GithubTaskSource(HttpGithubClient(...))` when
  `--github-repo`/`GITHUB_TOKEN` are present. Without them it runs as before.
- **`Harness.run()`**: `asyncio.gather` also receives the source loops (a tick = `poll`
  + `inbox.put`, otherwise it sleeps `poll_interval`).

## Error states (added to phase 2)

| Situation | Detection | Where |
|---|---|---|
| `poll()` raises (GitHub down) | exception in `SourcePoller.tick` | log an event, the tick returns False, the loop sleeps and tries again |
| `report_progress`/`finish` raises | `CompositeEventSink` isolates it | traceback to stderr, the loop keeps going |
| task with no `data.source` in the reflector | adapter `_mine()` → False | silently ignored |

## UI

The board **doesn't change**. `data.source` is visible in the task detail as part of
`data`. (A link to the issue on the card is an optional follow-up, not part of phase 4.)

## Code structure (additions)

```
src/harness/
  ports/
    source.py            # TaskSource, Progress, FinishResult
  source_poller.py       # SourcePoller (core, knows only ports)
  drivers/
    memory.py            # + MemoryTaskSource
    source_reflector.py  # SourceReflectorSink(EventSink)
    github_client.py     # GithubClient, Issue, FakeGithubClient, HttpGithubClient
    github_source.py     # GithubTaskSource
  app.py                 # wiring sources + reflector
  cli.py                 # --github-repo/--github-label; HttpGithubClient
```

## Invariants — new/refined

They extend the list in `CLAUDE.md`, they don't replace it.

13. **The outer world of tasks is one port `TaskSource` (`poll`/`report_progress`/
    `finish`).** GitHub is a driver; how the status is rendered (a label) is known only
    to the driver.
14. **A task's origin lives in `task.data.source`.** The projection outward reads it
    from there, not from side state. The router/dispatcher don't read `data.source`.
15. **`TaskSource` is touched only by `SourcePoller` and `SourceReflectorSink`**, wired
    in `app.py`. `dispatcher.py`/`consumer.py` don't import it.
16. **The projection outward is idempotent and doesn't block decision-making.**
    `report_progress` twice is a no-op; `CompositeEventSink` isolates failure.

## Completion check

Phase 4 is done when:

1. `FakeGithubClient` has an issue with the `harness:todo` label; after `poll()` the
   issue has `harness:queued` (not `todo`) and a `Task` with `data.source.issue` was
   created.
2. A second `poll()` **does not return** the same issue (the claim holds).
3. The task flows through the loop; on `dispatched` into steps the label changes per
   `step_labels`.
4. After reaching `end` the issue has `harness:pr-open`; after falling into `failed/` it
   has `harness:failed`.
5. A task from `harness submit` (with no `data.source`) passes through with not a single
   GitHub call.
6. E2E on in-memory drivers (Memory queue/workspace/artifacts/forge, FakeClock,
   `MemoryTaskSource`): issue-equivalent → task → PR → terminal projection.
7. Architecture tests: `dispatcher`/`consumer` don't import `ports/source`; `SourcePoller`
   imports only ports; nobody but `app.py`/`cli.py` touches the drivers.
8. `HttpGithubClient` exists and adds no production dependency (stdlib only).
