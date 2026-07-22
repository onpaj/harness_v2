# Design: archive a task off the board once its PR resolves

> This is a fresh design pass for a **second** retried attempt at this task
> (the `development` step timed out again before writing any code — same
> failure mode as the first retry, unrelated to the design). This document
> supersedes `design-02.md` for downstream steps; it restates the design in
> full against `plan-03.md` rather than pointing back at the superseded plan.
> I re-verified directly against current source rather than trusting
> `plan-03.md`'s claims: `git log` on every file this design touches
> (`models.py`, `ports/forge.py`, `consumer.py`, `dispatcher.py`,
> `projection.py`, `drivers/projection_events.py`, `source_poller.py`,
> `app.py`, `cli.py`, `drivers/fs_queue.py`, `drivers/fake_forge.py`,
> `drivers/memory.py`, `drivers/github_client.py`, `drivers/github_forge.py`,
> `behaviors/landing.py`, `task_control.py`, `router.py`,
> `tests/test_architecture.py`) still stops at `0e6f5d8` and `git status` is
> clean — the same commit `design-02.md`/`architecture-02.md` were written
> against. I re-read both "\[mandatory\]" fixes directly in the current tree:
> `src/harness/projection.py:96-98` still does the unconditional
> `self._columns[task_id] == name` inside `snapshot()`'s column loop, and
> `src/harness/app.py:121-126`'s `recover()` still walks only `[self._inbox,
> *self._step_queues.values()]` with `self._done` absent. Nothing has drifted
> across two retries now. Every signature, schema and call sequence below is
> carried forward unchanged from `design-02.md` (which itself carried forward
> `design-01.md` verbatim per `architecture-01.md`'s approval, confirmed still
> unaltered by `architecture-02.md`) — this is a restatement against the
> current plan number, not a redesign.

No UX/UI section — this feature has no user-facing surface of its own. The
board (`api/routes.py`, `board.html`/`_columns.html`) already renders whatever
`BoardView.snapshot()` returns, generically, with no per-column special-casing.
Once `BoardProjection` stops listing an archived task in any column, it
disappears from the dashboard on the next poll/SSE tick with zero front-end
changes — the same "free" render `plan-03.md` (Interfaces) notes.

This document assumes `plan-03.md`'s FRs/data model as the spec (identical in
substance to `plan-01.md`/`plan-02.md`'s, with all open questions now recorded
as decisions rather than proposals) and fills in concrete signatures, call
sequences, and the implementation details the plan's data model deliberately
leaves to design. Both items architecture-01/02 confirmed as real bugs
requiring a fix are marked **\[mandatory\]** — a naive implementation of
"leave the board" either breaks the whole board (`snapshot()`) or silently
loses a task on crash (`recover()`) without them, so both fixes must ship in
the same commit as the feature that exposes the gap they close, not as a
follow-up.

## Component design

### 1. `Forge.pull_request_state` — querying resolved state

`ports/forge.py` gains a tri-state enum and one new abstract method, mirroring
`open_pull_request`'s existing shape (task in, port-level exception out):

```python
class PullRequestState(str, Enum):
    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"          # closed, not merged

class Forge(ABC):
    @abstractmethod
    def open_pull_request(self, task: Task, *, branch: str, title: str, body: str) -> PullRequest: ...

    @abstractmethod
    def pull_request_state(self, task: Task) -> PullRequestState:
        """Current state of the PR referenced by task.data["pr"].
        Raises whatever the driver raises on failure (GithubForge: ForgeError) —
        PrWatcher treats any exception here as a transient failure to isolate,
        same as SourcePoller does around TaskSource.poll()."""
```

The method takes `task`, not a bare PR number, because every existing `Forge`
method already resolves identity from the task (`open_pull_request` derives
the repo slug from `task.repository`/`task.worktree`) — `pull_request_state`
follows the same shape and additionally reads `task.data["pr"]["number"]`.

Implementations:

- **`GithubForge.pull_request_state`** — reuses `_repo_path`/`_slug_of` exactly
  as `open_pull_request` does to get `slug`, then reads
  `task.data["pr"]["number"]`. Calls a new `GithubClient.get_pull_request`
  (below). Raises `ForgeError` for: no token, repo not resolvable, no
  `task.data["pr"]`, or an API failure (same `_explain` wrapping as
  `open_pull_request`'s except-clause).
- **`FakeForge.pull_request_state`** — reads the matching record from
  `prs.json` by `branch` (schema extended, see Data schemas) and maps its
  `state`/`merged` fields to `PullRequestState`. Gains a test/smoke helper
  `close_pull_request(branch: str, *, merged: bool) -> None` that flips a
  record's `state` to `"closed"` and `merged` accordingly — this is what
  `tests/test_smoke_git.py`'s extension (plan-03.md, Dependencies and scope)
  uses to simulate GitHub resolving the PR.
- **`MemoryForge.pull_request_state`** — same idea over `self.opened`/a new
  `self._states: dict[str, PullRequestState]` keyed by branch, defaulting to
  `OPEN` for a branch with no recorded state change. Gains `close(branch: str,
  *, merged: bool) -> None` for unit tests.

### 2. `GithubClient.get_pull_request` — fetching by number, any state

Today `find_pull_request` hardcodes `state=open` (it's used only to make
`open_pull_request` idempotent). A new abstract method fetches one PR by
number regardless of state:

```python
@dataclass(frozen=True)
class PullRequestDetail:
    number: int
    url: str
    head: str
    state: str        # "open" | "closed", exactly as GitHub's API reports it
    merged: bool       # true only when state == "closed" and GitHub merged it

class GithubClient(ABC):
    @abstractmethod
    def get_pull_request(self, repo: str, *, number: int) -> PullRequestDetail: ...
```

- **`HttpGithubClient.get_pull_request`** — `GET /repos/{repo}/pulls/{number}`,
  parses `state` and `merged` straight off the response body (both are native
  fields on GitHub's PR resource — no extra call needed).
- **`FakeGithubClient`** — internally tracks `self._pr_state: dict[int, tuple[str,
  bool]]`, defaulting each created pull to `("open", False)`. `get_pull_request`
  returns a `PullRequestDetail` built from that. Gains
  `close_pull_request(number: int, *, merged: bool) -> None` for unit tests
  (`GithubForge`/`GithubClient` test coverage) — distinct from `FakeForge`'s
  helper of the same name; one drives the client layer, one the forge layer.

`GithubForge.pull_request_state` maps `PullRequestDetail` → `PullRequestState`:
`state == "open"` → `OPEN`; `state == "closed" and merged` → `MERGED`;
`state == "closed" and not merged` → `CLOSED`.

### 3. Persisting the PR reference: `BehaviorResult.data`

`models.py`'s `BehaviorResult` gains an optional third field:

```python
@dataclass(frozen=True)
class BehaviorResult:
    outcome: Outcome
    summary: str = ""
    data: dict[str, Any] | None = None
    """Extra fields the consumer merges into task.data on delivery. None (the
    default) merges nothing — every existing behavior is unaffected."""
```

`Consumer._deliver` (`consumer.py`) merges it in before building `updated`:

```python
merged_data = {**task.data, **result.data} if result.data else task.data
updated = append_history(
    replace(task, data=merged_data, last_outcome=result.outcome.value, lock_id=None),
    entry,
)
```

This is a shallow merge (matches how `task.data` is already treated
elsewhere — e.g. `data.source` — as a flat bag of independent keys). No branch
on `outcome`'s *value* is introduced (`test_consumer_has_no_branch_on_outcome_value`
stays satisfied — this is an unconditional `if result.data else task.data`, not
a comparison against `outcome`). Architecture-01/02 checked this specifically
against the `ast`-based guard (`tests/test_architecture.py:175-194`) and
confirmed an `if result.data:` check doesn't touch `outcome` at all, so it
cannot trip it.

`LandingBehavior.run` (`behaviors/landing.py`) attaches the reference right
after the PR is opened:

```python
pull = self._forge.open_pull_request(task, branch=handle.branch, title=..., body=...)
return BehaviorResult(
    Outcome.DONE,
    f"opened PR {pull.url}",
    data={"pr": {"number": pull.number, "url": pull.url, "branch": pull.branch}},
)
```

A task landed before this ships never got this key written, so `task.data.get("pr")`
is `None` for it — this is what makes FR-2's second AC (no crash, no retry
storm) fall out for free rather than needing a special case.

### 4. `PrWatcher` — the new core component

New module `pr_watcher.py`, same architectural tier as `source_poller.py`:
core, touches only `ports/{queue,forge,events,clock}` and `models` — never a
driver. Constructed once in `build()`, ticked on its own loop in `Harness.run()`.

```python
ACTOR = "pr_watcher"

class PrWatcher:
    def __init__(self, *, done: TaskQueue, archived: TaskQueue, forge: Forge,
                 events: EventSink, clock: Clock) -> None: ...

    def tick(self) -> bool:
        """Check every done task with a stored PR reference. True if anything archived."""
        archived_any = False
        for task in self._done.list():
            pr = task.data.get("pr")
            if not isinstance(pr, dict):
                continue                                   # never landed, or pre-feature task
            try:
                state = self._forge.pull_request_state(task)
            except Exception as error:  # noqa: BLE001 - one bad check must not stop the tick
                self._events.emit("pr_watch_error", task_id=task.id, error=str(error))
                continue
            if state is PullRequestState.OPEN:
                continue                                    # still open, check again next tick
            if self._archive(task, state):
                archived_any = True
        return archived_any

    def _archive(self, task: Task, state: PullRequestState) -> bool:
        claimed = self._done.claim(task, new_lock_id())
        if claimed is None:
            return False                                    # lost a race (e.g. concurrent tick)
        resolution = "merged" if state is PullRequestState.MERGED else "closed"
        entry = HistoryEntry(
            at=self._clock.now(), actor=ACTOR,
            from_step=DONE_COLUMN, to_step=None, reason=f"pr {resolution}",
        )
        resolved = append_history(replace(claimed, status=ARCHIVED, lock_id=None), entry)
        self._done.transfer(resolved, self._archived)
        self._events.emit(
            "archived", task_id=task.id, resolution=resolution,
            queue=ARCHIVED, task=resolved.to_dict(),
        )
        return True
```

`status` on archival. Today `end`/`failed` are each a status value that lines
up 1:1 with a terminal queue. Archiving introduces a third: `ARCHIVED =
"archived"` (new constant in `models.py`, alongside `END`/`FAILED`). The
task's `status` moves from `"end"` to `"archived"` on the transfer — purely
informational (nothing routes on it, since nothing ever claims out of
`archived/`), but it means a task fetched by id post-archival is
self-describing without having to scan its history for the reason.

**\[mandatory\] Loop-through-`.processing/` recovery gap.** `done/` and
`failed/` are documented as "queues nobody consumes" — `Harness.recover()`
accordingly only recovers `[inbox, *step_queues.values()]`. `PrWatcher` breaks
that assumption for `done/`: it now *does* claim from it. A crash between
`self._done.claim(...)` and `self._done.transfer(...)` would strand the task
in `done/.processing/`, invisible to `done.list()` (`FilesystemTaskQueue.list()`
globs `self._root`, not `self._processing`) and therefore to
`BoardProjection.hydrate()` — a silent, permanent loss on restart, worse than
the bug this feature fixes. Re-verified directly against the current source
this pass (`app.py:121-126`, `dispatcher.py:99-111`, `drivers/fs_queue.py:59-65`)
and confirmed the gap is still real, unchanged since architecture-01/02 first
flagged it — this fix ships in the same commit as `PrWatcher`'s wiring into
`Harness`, not separately, since the bug doesn't exist until `PrWatcher`
claims from `done`. Fix: `Harness.recover()` includes `self._done` in the
queues it recovers:

```python
def recover(self) -> int:
    queues = [self._inbox, *self._step_queues.values(), self._done]
    ...
```

Harmless for installs with no `PrWatcher` wired (or interval `0`, see §5) —
`done/.processing/` is simply always empty for them, so `recover()` finds
nothing there, exactly as today. Invariant #6 (`recover()` before `hydrate()`)
still holds and is what makes this safe — the call order in `Harness.run()`
already puts `recover()` first, re-verified.

### 5. Wiring: `archived/` queue, `Harness`, `build()`, `cli.py`

- **`HarnessLayout`** (`app.py`) gains `archived -> root / "archived"`,
  parallel to `done`/`failed`.
- **`build()`** constructs `archived = FilesystemTaskQueue(name="archived",
  root=layout.archived, events=events)` (no `quarantine` — same as `done`/`failed`
  today) and always constructs a `PrWatcher(done=done, archived=archived,
  forge=forge, events=events, clock=clock)` — cheap and side-effect-free until
  ticked, exactly like `landing` is always built even when the workflow's last
  step isn't `land`. `forge` already exists in every `build()` call (defaults
  to `MemoryForge()`), so no new required parameter.
- **`Harness.__init__`** gains `archived: TaskQueue` and `pr_watcher:
  PrWatcher` parameters; stores both. `run()` gains `pr_poll_interval: float =
  0.0` and:
  - passes `archived=self._archived` into `self.projection.hydrate(...)`
    (see §6),
  - only adds `self._pr_watcher_loop(...)` to the `asyncio.gather(...)` set
    when `pr_poll_interval > 0` — the loop coroutine itself is the same
    tick-or-sleep shape as `_source_loop`.

  ```python
  async def _pr_watcher_loop(self, interval: float, stop: asyncio.Event) -> None:
      while not stop.is_set():
          if not self.pr_watcher.tick():
              await asyncio.sleep(interval)
          else:
              await asyncio.sleep(0)
  ```

FR-6 "off by default." `--pr-poll` follows the `--api-port 0` convention
already in this codebase (`0` = feature disabled) rather than introducing a
second `--enable-pr-watcher` flag: `cli.py`'s `run` subcommand gains
`run.add_argument("--pr-poll", type=float, default=0.0, dest="pr_poll",
help="interval (s) for archiving landed tasks whose PR has resolved; 0 disables it (default)")`.
`_run()`/`serve()` thread it to `harness.run(pr_poll_interval=args.pr_poll,
...)`. Default `0` means an existing `harness run` invocation with no new flag
behaves identically to today — the FR's "off by default" is literal, not just
"harmless by default" (which would also have been true, since pre-feature
tasks carry no `data["pr"]` — but an explicit off-switch is the safer, more
legible reading of the requirement).

### 6. `BoardProjection`: leaving the board without disappearing from `get()`

Two changes to `projection.py`, both needed together — the plan's data model
names the destination ("dropped from the columns index but kept queryable by
id") but the current `snapshot()`/`hydrate()` implementation can't do that
as-is without also changing:

**a) \[mandatory\] `snapshot()`'s column loop currently indexes
`self._columns[task_id]` unconditionally while iterating `self._tasks.items()`
(`projection.py:96-98`).** Re-verified against the current tree this pass —
still present verbatim. An archived task sits in `_tasks` with no entry in
`_columns` (see (b)), so that line raises `KeyError`, not just "fails to list
the task." Unfixed, the very first `snapshot()` call after any archive takes
down the *whole* board, not just that one task's listing — this makes the fix
mandatory, and it must ship in the same commit as `archive()`, not as a
follow-up. It must tolerate a missing column:

```python
tasks = tuple(sorted(
    (task for task_id, task in self._tasks.items() if self._columns.get(task_id) == name),
    key=lambda task: (task.created, task.id),
))
```

**b) A new `archive()` entry point, plus a shared "register without listing"
primitive** used both live (via the event stream) and at startup (via
`hydrate()`, so a restart doesn't lose `get()`-ability for tasks archived in a
previous run — the plan's FR-4 AC says "remains retrievable," not "remains
retrievable until the next restart"):

```python
def hydrate(self, *, inbox, step_queues, done, failed, archived: TaskQueue | None = None) -> None:
    ...                                    # unchanged for inbox/step_queues/done/failed
    if archived is not None:
        for task in archived.list():
            self._register(task)           # queryable by id, no column — never listed
    self._bump()

def archive(self, task: Task) -> None:
    """Task resolved (PR merged/closed): drop from every column, keep gettable by id."""
    self._register(task)
    self._bump()

def _register(self, task: Task) -> None:
    self._tasks[task.id] = task
    self._columns.pop(task.id, None)
```

`archived` is an optional `hydrate()` parameter (not a required one) so
existing callers/tests that construct a `BoardProjection` without an archived
queue (there are none in the current wiring, but unit tests that hand-build a
projection directly) don't need to change.

A unit test must exercise `archive()` immediately followed by `snapshot()`
(not just `archive()` followed by `get()`), since that ordering is exactly
what a naive implementation of this design would get wrong — the one
sequencing mistake that turns a listing bug into an outage.

### 7. Event flow: `ProjectionSink` reacts, `SourceReflectorSink` ignores

`drivers/projection_events.py`'s `ProjectionSink.emit` currently has one path
(`apply(column, task)`) driven purely by the presence of `queue`/`task`
fields. It gains a name-based branch for the one event that isn't a plain
column move:

```python
def emit(self, name: str, **fields: Any) -> None:
    raw = fields.get("task")
    if not isinstance(raw, dict):
        return
    try:
        task = Task.from_dict(raw)
    except (KeyError, TypeError):
        return

    if name == "archived":
        self._projection.archive(task)
        return

    column = fields.get("queue")
    if not isinstance(column, str):
        return
    self._projection.apply(column, task)
```

`SourceReflectorSink` needs **no change**: its `emit` already falls through
untouched for any `name` outside `{"dispatched", "finished", "failed"}` — a
PR closing unmerged deliberately leaves the source issue exactly as `land`
left it (plan-03.md, Out of scope / Open questions — issue-side signaling is
explicitly deferred and recorded as decided, not merely proposed).

### 8. The new core module's import boundary — a guard test, not just a convention

`tests/test_architecture.py` guards `source_poller.py`'s import boundary *by
name* (`test_source_poller_imports_only_ports_and_models`) — there is no
generic "every core module obeys this" check, the guard is per-file.
Without a parallel test, `pr_watcher.py` gets **no automatic protection**
against a future edit importing `drivers.github_forge` (or any other driver)
directly "just this once." This design treats the guard test as part of the
component, not an afterthought:

- Add `test_pr_watcher_imports_only_ports_and_models` to
  `tests/test_architecture.py`, structurally identical to the existing
  `test_source_poller_imports_only_ports_and_models`, pointed at
  `pr_watcher.py`. Ships in the same commit as `pr_watcher.py` itself — the
  module and its boundary guard are one unit of work, exactly like invariant
  #17's existing per-file guards for `source_poller.py`.

### 9. Sequence, end to end

```
land (Consumer)                 dispatcher            PrWatcher (own loop)          BoardProjection
────────────────                ──────────            ────────────────────          ────────────────
LandingBehavior.run
  forge.open_pull_request  ──►  PullRequest(number, url, branch)
  return BehaviorResult(
    DONE, data={"pr": {...}})
Consumer._deliver
  task.data["pr"] = {...}  ──►  event "consumed" (queue="land")
                                 dispatcher.tick()
                                 route() -> Finished
                                 _finish(): status="end"  ──► event "finished" (queue="done")
                                                                                    apply("done", task)
                                                                       ...          [task visible in
                                                                                     "done" column]
                                                              tick():
                                                                task in done.list()
                                                                has data["pr"]
                                                                forge.pull_request_state(task)
                                                                -> MERGED / CLOSED
                                                                claim, append history,
                                                                status="archived"
                                                                done -> archived  ──► event "archived"
                                                                                    archive(task)
                                                                                    [dropped from
                                                                                     "done"; still
                                                                                     BoardView.get()able]
```

## Data schemas

### `PullRequestState` (`ports/forge.py`, new)

```python
class PullRequestState(str, Enum):
    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"    # closed, not merged
```

### `PullRequestDetail` (`drivers/github_client.py`, new)

```python
@dataclass(frozen=True)
class PullRequestDetail:
    number: int
    url: str
    head: str        # "owner:branch"
    state: str        # "open" | "closed" — GitHub's raw field
    merged: bool
```

### `Task.data["pr"]` (new key, written by `LandingBehavior`)

```jsonc
{
  "pr": { "number": 42, "url": "https://github.com/onpaj/repo/pull/42", "branch": "harness/tsk_..." }
}
```

Absent entirely on any task landed before this ships, or on a task whose
workflow has no `land` step — both read as "nothing to watch," not an error.

### `prs.json` record (`FakeForge`, schema extended)

```jsonc
{
  "number": 1,
  "url": "file:///.../prs.json#1",
  "branch": "harness/tsk_...",
  "title": "...",
  "body": "...",
  "state": "open",     // new — "open" | "closed", default "open" for pre-existing records
  "merged": false        // new — meaningful only when state == "closed"
}
```

A record loaded from a `prs.json` written before this feature shipped has no
`state`/`merged` keys; `FakeForge._load`/`_to_pr`-adjacent read path defaults
missing `state` to `"open"` and missing `merged` to `false`, so an old fixture
file keeps behaving as "still open" rather than erroring — this matches how
the rest of `fake_forge.py`/`fs_queue.py` already treats "absent old field" as
"default", not "error" (e.g. `Task.from_dict`'s `raw.get(...)` throughout
`models.py`).

### `BehaviorResult` (`models.py`, extended)

```python
@dataclass(frozen=True)
class BehaviorResult:
    outcome: Outcome
    summary: str = ""
    data: dict[str, Any] | None = None   # new, default None — merged into task.data by Consumer
```

### `HistoryEntry` for an archival (shape, no schema change — uses existing fields)

```jsonc
{
  "at": "2026-07-21T10:00:00Z",
  "actor": "pr_watcher",
  "from": "done",
  "to": null,
  "reason": "pr merged"    // or "pr closed"
}
```

Parallel to `TaskControlService.restart`'s entry (`actor="operator"`,
`to_step=None`, `reason="restarted by operator"`) — `to_step=None` because,
like a restart, this isn't a workflow-graph edge (`router.route()` never
produces it), it's an out-of-band mutation the same tier of code as the
dispatcher and task-control service is allowed to make.

### Event `"archived"` (new, parallel to `"finished"`/`"failed"`/`"restarted"`)

```jsonc
{
  "task_id": "tsk_...",
  "resolution": "merged",   // or "closed"
  "queue": "archived",
  "task": { /* full Task.to_dict(), status="archived" */ }
}
```

### Event `"pr_watch_error"` (new, parallel to `"source_error"`)

```jsonc
{ "task_id": "tsk_...", "error": "GitHub refused to fetch pull request 42: ..." }
```

Per-task (unlike `source_error`, which is per-poll-call) because
`Forge.pull_request_state` is called once per task inside the tick's loop, not
once for the whole tick — one task's forge failure must not stop the rest of
that tick's tasks from being checked (FR-3's AC).

### `Task.status` (new value)

`ARCHIVED = "archived"` (new constant in `models.py`, alongside `END`/`FAILED`).
Set by `PrWatcher._archive` on the transfer out of `done/`. Not read by
`router.route()` or `Dispatcher` (both only ever see tasks from `inbox`, whose
`status` is always a workflow step name or `None` — an archived task is never
reintroduced to the inbox), so this introduces no new routing case, only a
more legible `task.to_dict()["status"]` for a task fetched by id after
archival.

### CLI flag (`cli.py`, `run` subcommand)

```
--pr-poll <seconds>    default 0.0 — interval for archiving landed tasks whose
                        PR has resolved (merged or closed unmerged); 0 disables
                        the watcher entirely, same convention as --api-port 0
```

## Summary of what changed relative to `design-02.md`

Nothing in the signatures, schemas or call sequences changed —
architecture-02 approved `design-02.md` verbatim and found nothing to alter,
and this pass's fresh re-verification against current source (still `0e6f5d8`,
both mandatory fixes still present verbatim) confirms nothing has drifted
since. This document:

1. Restates everything against `plan-03.md` instead of the superseded
   `plan-02.md`.
2. Carries forward both **\[mandatory\]** fixes (`snapshot()`'s `KeyError`,
   `Harness.recover()`'s missing `done` queue) and §8's import-boundary guard
   test unchanged, with the verified-against-source evidence refreshed for
   this retry (§4, §6a).
3. Records the plan's open questions as settled decisions throughout (poll
   interval 300s, archive-not-delete, issue-side signaling out of scope, no
   backfill) rather than re-deriving them — matching `plan-03.md`'s own
   framing that these were resolved across two prior passes, not reopened.
