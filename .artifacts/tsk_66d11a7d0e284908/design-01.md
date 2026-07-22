# Design: Poll for merged PRs and remove their completed tasks from the dashboard

Grounds `plan-01.md` (the spec) in the actual shapes of `models.py`, `projection.py`,
`app.py`, `cli.py`, `ports/{queue,forge,board}.py`, `drivers/{github_client,github_forge,
memory,fake_forge,projection_events}.py`. No new UI surface is introduced — the existing
board (`/api/board`, `/api/tasks/{id}`, the htmx templates) already renders whatever
`BoardProjection` reports; this design only changes what gets reported. There is
therefore no UX/UI section.

## 1. Component design

### 1.1 `BehaviorResult.data` + consumer merge

`models.py`:

```python
@dataclass(frozen=True)
class BehaviorResult:
    outcome: Outcome
    summary: str = ""
    data: dict[str, Any] | None = None
```

`consumer.py::Consumer._deliver` merges it into `task.data` before appending history:

```python
merged_data = {**task.data, **(result.data or {})}
updated = append_history(
    replace(task, data=merged_data, last_outcome=result.outcome.value, lock_id=None),
    entry,
)
```

This is a shallow merge at the top level (`result.data` keys overwrite same-named keys
on `task.data`; `LandingBehavior` only ever sets `"pr"`, so no collision with
`"source"`/`"title"`/etc.). No branch on `outcome` is introduced — `test_consumer_has_
no_branch_on_outcome_value` stays satisfied, and this is a pure data-plumbing change,
not a decision.

### 1.2 `PullRequest.repo` + `task.data["pr"]`

`ports/forge.py`:

```python
@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    branch: str
    title: str
    repo: str
```

`repo` becomes a required field (not optional) — every `Forge` implementation already
knows its own repo identity at the point it constructs a `PullRequest`:

- `GithubForge.open_pull_request` passes `repo=slug` (it already computes `slug`
  locally).
- `MemoryForge`/`FakeForge` gain a `repo` parameter to `open_pull_request`... no —
  they don't receive one from the port signature. Instead they synthesize a stable
  placeholder the same way they synthesize `url`: `MemoryForge` uses
  `f"memory/{branch}"`, `FakeForge` uses `f"local/{branch}"`. This satisfies FR-1's
  second AC ("still gets a `data.pr` block ... the reconciler simply never reports it
  merged, since there is nothing behind `repo` to poll") — a fake repo string is never
  a real GitHub slug, so `GithubMergeChecker.is_merged` on such a task would 404/error;
  it is simply never wired to a real `MergeChecker` in the first place (`merge_checker`
  is only constructed in `cli.py` alongside `GithubForge`, never alongside `FakeForge`/
  `MemoryForge` — see 1.7).

`behaviors/landing.py::LandingBehavior.run` builds the structured block once it has the
`PullRequest` back, and returns it via `BehaviorResult.data`:

```python
pull = self._forge.open_pull_request(task, branch=handle.branch, title=..., body=...)
return BehaviorResult(
    Outcome.DONE,
    f"opened PR {pull.url}",
    data={"pr": {"repo": pull.repo, "number": pull.number, "url": pull.url, "branch": pull.branch}},
)
```

No branching on `outcome` — `LandingBehavior` never returns `REQUEST_CHANGES`, this is
a straight-line addition to the existing single return.

### 1.3 `ports/merge.py` — the `MergeChecker` port

New file, mirroring the read/write split already established by `BoardView`/
`TaskControl`:

```python
class MergeChecker(ABC):
    @abstractmethod
    def is_merged(self, task: Task) -> bool | None:
        """True once merged, False while open, None if the task carries no `data.pr`.

        A transient failure (network, API) must raise — the caller needs to tell
        "not merged" apart from "couldn't check" so it can retry instead of never
        archiving a merged task."""
```

Deliberately not a method on `Forge`: `Forge` is the write side of landing (invariant:
"the merge strategy is a human's call" — the harness only *proposes*); `MergeChecker`
is a new, independent read capability, so a caller that only needs one doesn't have to
depend on the other (the same reasoning as `BoardView` vs. `TaskControl`).

### 1.4 `drivers/github_client.py` — `get_pull_request`

Extends `GithubClient` with one more verb and a new value type:

```python
@dataclass(frozen=True)
class PullRequestDetail:
    number: int
    url: str
    merged: bool

class GithubClient(ABC):
    ...
    @abstractmethod
    def get_pull_request(self, repo: str, number: int) -> PullRequestDetail: ...
```

`HttpGithubClient.get_pull_request` — `GET /repos/{repo}/pulls/{number}`, reads the
`merged` boolean straight from the response body. Any `urllib.error.HTTPError` or
malformed response propagates unchanged (no `try/except` here) — that propagation is
exactly what makes `MergeChecker.is_merged`'s "raise on transient failure" contract
true for free.

`FakeGithubClient` gains a `merged: set[int]` (PR numbers) and a helper `merge_pull_
request(number)` for tests to flip a fake PR to merged; `get_pull_request` looks up
`self.pulls` by number (raising `KeyError` — a lookup failure — if the number is
unknown, same "raise rather than lie" contract as the real client).

### 1.5 `drivers/github_merge_checker.py` — `GithubMergeChecker`

```python
class GithubMergeChecker(MergeChecker):
    def __init__(self, client: GithubClient) -> None:
        self._client = client

    def is_merged(self, task: Task) -> bool | None:
        pr = task.data.get("pr")
        if not isinstance(pr, dict):
            return None
        repo, number = pr.get("repo"), pr.get("number")
        if not isinstance(repo, str) or not isinstance(number, int):
            return None
        return self._client.get_pull_request(repo, number).merged
```

A test double, `FakeMergeChecker` (in `drivers/memory.py`, alongside `MemoryForge`),
gives unit tests direct control without going through a fake `GithubClient`:

```python
class FakeMergeChecker(MergeChecker):
    def __init__(self) -> None:
        self.merged: set[tuple[str, int]] = set()
        self.raises: set[tuple[str, int]] = set()

    def is_merged(self, task: Task) -> bool | None:
        pr = task.data.get("pr")
        if not isinstance(pr, dict):
            return None
        key = (pr.get("repo"), pr.get("number"))
        if key in self.raises:
            raise RuntimeError(f"merge check failed for {key}")
        return key in self.merged
```

### 1.6 `merge_reconciler.py` — the `MergeReconciler` core loop

Same shape as `SourcePoller`: a core module that knows only ports and models (a new
`test_reconciler_imports_only_ports_and_models` extends `test_architecture.py` the same
way `test_source_poller_imports_only_ports_and_models` does).

```python
ACTOR = "merge_reconciler"

class MergeReconciler:
    def __init__(self, *, done: TaskQueue, archived: TaskQueue,
                 checker: MergeChecker, events: EventSink, clock: Clock) -> None:
        self._done, self._archived = done, archived
        self._checker, self._events, self._clock = checker, events, clock

    def tick(self) -> bool:
        candidates = [t for t in self._done.list() if isinstance(t.data.get("pr"), dict)]
        if not candidates:
            return False
        candidates.sort(key=lambda t: (t.data["pr"].get("checkedAt") or "", t.created, t.id))
        task = self._done.claim(candidates[0], new_lock_id())
        if task is None:
            return False

        try:
            merged = self._checker.is_merged(task)
        except Exception as error:
            self._release(task, checked_at=None)
            self._events.emit("merge_check_error", task_id=task.id, error=str(error))
            return False

        if not merged:
            self._release(task, checked_at=self._clock.now())
            return False

        entry = HistoryEntry(at=self._clock.now(), actor=ACTOR, from_step=task.status,
                              to_step="archived",
                              summary=f"PR {task.data['pr'].get('url', '')} merged")
        updated = append_history(replace(task, lock_id=None), entry)
        self._done.transfer(updated, self._archived)
        self._events.emit("archived", task_id=updated.id, queue="archived", task=updated.to_dict())
        return True

    def _release(self, task: Task, *, checked_at: str | None) -> None:
        pr = dict(task.data["pr"])
        if checked_at is not None:
            pr["checkedAt"] = checked_at
        released = replace(task, lock_id=None, data={**task.data, "pr": pr})
        self._done.transfer(released, self._done)
```

**Selection is least-recently-checked, not "first in `done.list()`".** A naive
first-candidate-every-time selection has a starvation bug: after `tick()` releases a
still-open-PR task back into `done` (same position, same sort key), the very next
`tick()` would pick the *same* task again forever, and a second `done` task with a
`data.pr` would never be examined as long as the first stays unmerged. Stamping
`checkedAt` on every non-error examination and sorting ascending on it (unset sorts
first) makes selection round-robin: whichever candidate has waited longest since its
last check goes next. A checker error deliberately does **not** stamp `checkedAt`, so a
task that failed to check is retried before tasks that were successfully checked (it
gets, in effect, priority to catch up rather than being pushed to the back).

**`tick()` returns `True` only when a task is actually archived** (a real state
change), `False` in every other case — no candidate, lost claim race, checker error,
or "checked, still open". This mirrors `SourcePoller` closely enough to reuse
`Harness`'s `if not tick(): sleep(interval) else: sleep(0)` loop shape, but is a
deliberate choice distinct from "return True whenever work was done": returning `True`
for "examined, not merged" would let the loop tight-spin (`sleep(0)`) across every
open-PR task in `done` before ever backing off, defeating the "gentle on GitHub's rate
limit" requirement. Practically: each `reconcile_interval` tick examines exactly one
candidate (least-recently-checked), so with `N` outstanding `done` tasks a given task
is re-checked roughly every `N × reconcile_interval` — acceptable, since this is a
housekeeping sweep, not a latency-sensitive path. A burst of consecutive merges *does*
still get archived quickly: each successful archive returns `True`, so the loop
`sleep(0)`s straight into examining the next candidate.

**Same-queue release is safe.** `_release` calls `self._done.transfer(released, self._
done)` — releasing a claimed task back into the queue it came from. `FilesystemTaskQueue.
transfer` handles `destination is self` the same as any other `FilesystemTaskQueue`
destination (`.processing/<id>.json` → `<queue-root>/<id>.json` via `os.replace`, which
works identically whether source and destination directory are the same queue or not),
so no special-casing is needed in `MergeReconciler` or the driver.

### 1.7 Wiring: `HarnessLayout`, `Harness`, `app.py::build()`, `cli.py`

**`HarnessLayout`** gains `archived -> Path` (`root / "archived"`), alongside `done`/
`failed`.

**`Harness`** gains two optional fields, both `None` when merge reconciliation isn't
wired (fully backward compatible — the same "omitted → current behavior unchanged"
shape as `sources`/`catalog`):

```python
archived: TaskQueue | None = None
reconciler: MergeReconciler | None = None
```

- `recover()` — the queue list gains `self._done` unconditionally: `[inbox, *step_
  queues.values(), done]`. This is required regardless of whether reconciliation is
  enabled *this* run — a task can be left claimed in `done/.processing/` by a crash
  during a previous run that had it enabled, or (once code is deployed) by any run at
  all. Recovering an idle `done` queue when nothing is claimed is a no-op, so this is
  free when the reconciler isn't running. This directly satisfies FR-3's crash-safety
  AC ("killing the process between `claim()` and `transfer()` ... `recover()` puts the
  task straight back into `done`").
- `run()` gains `reconcile_interval: float = 300.0` (see 2.3 for why 300s, not the
  30s `source_interval`) and, when `self.reconciler is not None`, adds one more
  gathered loop:

  ```python
  async def _reconcile_loop(self, reconciler, reconcile_interval, stop):
      while not stop.is_set():
          if not reconciler.tick():
              await asyncio.sleep(reconcile_interval)
          else:
              await asyncio.sleep(0)
  ```

- `self.projection.hydrate(..., archived=self._archived)` — see 1.8 for what `hydrate`
  does with it.

**`app.py::build()`** gains `merge_checker: MergeChecker | None = None`. When given:

```python
archived = FilesystemTaskQueue(name="archived", root=layout.archived, events=events)
reconciler = MergeReconciler(done=done, archived=archived, checker=merge_checker,
                              events=events, clock=clock)
```

passed into `Harness(..., archived=archived, reconciler=reconciler)`; when omitted,
both stay `None` and nothing else in `build()` changes.

**`cli.py`** — a new `_build_merge_checker(args) -> MergeChecker | None`, wired
alongside `_build_forge`/`_github_sources` in `_run()`:

```python
def _build_merge_checker(args) -> MergeChecker | None:
    token = os.environ.get("GITHUB_TOKEN")
    return GithubMergeChecker(HttpGithubClient(token)) if token else None
```

Unlike `_github_sources`, this needs no per-repo construction and no `RepositoryRegistry`
— `GithubMergeChecker.is_merged` reads `repo` straight off `task.data["pr"]`, resolved
at check time, not at construction time. It is `None` exactly when `GithubForge` would
also refuse to open a PR (no token) — reconciliation only ever exists for tasks that a
real forge landed. `--forge fake` implies `merge_checker=None` too, since `_build_merge_
checker` only looks at `GITHUB_TOKEN`, independent of `--forge`.

A new `--reconcile-poll` CLI flag (`dest="reconcile_poll"`, default `300.0`), threaded
through `serve()` into `harness.run(reconcile_interval=args.reconcile_poll)`, the same
way `--source-poll` already threads into `source_interval`.

### 1.8 `BoardProjection` — hiding archived tasks

`hydrate()` gains an optional `archived: TaskQueue | None = None` parameter. Tasks in
it go into `_tasks` **without** a `_columns` entry — a new private path, not `_store`
(which would refuse them anyway: `"archived"` is not in `_order`, so today's `_store`
silently drops both the task *and* its column, which would break `get()` after a
restart):

```python
def hydrate(self, *, inbox, step_queues, done, failed, archived: TaskQueue | None = None) -> None:
    ...  # unchanged existing hydration
    if archived is not None:
        for task in archived.list():
            self._tasks[task.id] = task
    self._bump()
```

A new public method, the live-path equivalent:

```python
def archive(self, task: Task) -> None:
    """Keep the task fetchable by id; drop it out of every rendered column."""
    self._tasks[task.id] = task
    self._columns.pop(task.id, None)
    self._bump()
```

`snapshot()` is unchanged — it only ever iterates `_columns`, so a task present in
`_tasks` but absent from `_columns` simply never appears in any `BoardColumn`. `get()`
is unchanged too and keeps resolving archived tasks, satisfying FR-4's AC that `GET
/api/tasks/{id}` still returns 200 with full history and `data.pr`.

`drivers/projection_events.py::ProjectionSink` routes the new `"archived"` event to
`archive()` instead of `apply()` — it's the one event whose `queue` field
(`"archived"`) deliberately isn't a rendered column, so it can't go through the
existing `column`-based branch:

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
    if isinstance(column, str):
        self._projection.apply(column, task)
```

No endpoint changes in `api/routes.py` — `/api/board` and `/api/tasks/{task_id}`
already express exactly the right thing once `BoardProjection` behaves this way (per
spec, confirmed against the current route bodies: `board()` returns `view.snapshot().
to_dict()`, `task()` returns `view.get(task_id)`; neither needs to know that some tasks
are archived).

### 1.9 Architecture-test additions

Extends `tests/test_architecture.py`:

- `test_reconciler_imports_only_ports_and_models` — same shape as `test_source_poller_
  imports_only_ports_and_models`, applied to `merge_reconciler.py`.
- `dispatcher.py`/`consumer.py` must not import `harness.ports.merge` — extend the
  existing `test_orchestration_does_not_import_*` family (or add a one-off assertion
  next to `test_orchestration_does_not_import_source_port`, which is the closest
  precedent: a read-only outward-facing port that only a dedicated core loop and the
  wiring touch).
- `harness.drivers.github_merge_checker` joins the `WORK_DRIVERS`-style "driver, not
  orchestration" checks already covering `github_forge`/`github_source` implicitly via
  `test_only_app_and_cli_wire_drivers`.

## 2. Data schemas

### 2.1 `Task.data["pr"]` (written once by landing, extended by the reconciler)

```jsonc
{
  "repo": "onpaj/harness_v2",     // GitHub slug, or a non-GitHub placeholder for fake/memory forges
  "number": 45,
  "url": "https://github.com/onpaj/harness_v2/pull/45",
  "branch": "harness/tsk_66d11a7d0e284908",
  "checkedAt": "2026-07-21T20:15:00Z"   // optional; absent until the reconciler's first check
}
```

`repo`/`number`/`url`/`branch` are written once, atomically, by `LandingBehavior` via
`BehaviorResult.data`. `checkedAt` is the one field the reconciler itself writes back
(via `_release`), stamped only on a successful (non-raising) check — see 1.6.

### 2.2 `BehaviorResult` (extended)

```python
BehaviorResult(outcome=Outcome.DONE, summary="opened PR https://...",
               data={"pr": {"repo": ..., "number": ..., "url": ..., "branch": ...}})
```

`data` is `None` for every existing behavior (`DummyBehavior`, `ClaudeCliBehavior`) —
only `LandingBehavior` populates it. The consumer's merge (`{**task.data, **(result.
data or {})}`) is a no-op when `data` is `None`.

### 2.3 `PullRequest` (extended)

```python
PullRequest(number=45, url="https://github.com/onpaj/harness_v2/pull/45",
            branch="harness/tsk_...", title="...", repo="onpaj/harness_v2")
```

### 2.4 `ports/merge.py` — interface

```python
class MergeChecker(ABC):
    def is_merged(self, task: Task) -> bool | None: ...
```

### 2.5 `GithubClient.get_pull_request` — new verb + value type

```python
@dataclass(frozen=True)
class PullRequestDetail:
    number: int
    url: str
    merged: bool

def get_pull_request(self, repo: str, number: int) -> PullRequestDetail: ...
```

### 2.6 `archived/` queue — same `Task` JSON as `done/`/`failed/`

No schema change to the task file itself. One extra `HistoryEntry`:

```json
{"at": "2026-07-21T20:15:00Z", "actor": "merge_reconciler", "from": "end",
 "to": "archived", "summary": "PR https://github.com/onpaj/harness_v2/pull/45 merged"}
```

`task.status` stays `"end"` — archiving relocates which *queue*/*column* the task
belongs to, not its workflow status (the same separation `BoardProjection` already
relies on for the `todo` column, which shows `status=None` tasks under a column name
that isn't a real `status` value either).

### 2.7 New events on the `EventSink` stream

| event | fields | emitted by |
|---|---|---|
| `archived` | `task_id`, `queue="archived"`, `task` (full dict) | `MergeReconciler.tick()`, on a successful archive |
| `merge_check_error` | `task_id`, `error` (str) | `MergeReconciler.tick()`, on a `MergeChecker` exception — mirrors `SourcePoller`'s existing `source_error` |

## 3. Design decisions that settle the spec's open questions

- **Port shape**: confirmed — a dedicated `MergeChecker` port, separate from `Forge`
  (1.3).
- **`archived/` as a real queue**: confirmed — a plain `TaskQueue`, always constructible
  the same way `done`/`failed` are, but only instantiated in `build()` when a
  `merge_checker` is supplied; when it isn't, `Harness.archived`/`reconciler` are both
  `None` and nothing else changes (1.7).
- **Retention**: confirmed — indefinite, no expiry/GC, identical to `done`/`failed`
  today.
- **Reconcile interval**: **300 seconds (5 minutes)** default — settles the spec's
  "tens of seconds, TBD" question in favor of the originating issue's explicit ask
  ("deliberately a long interval (≈5 min) so we don't burn API rate limits"), and is
  distinct from the local `poll_interval` (0.2s) and `source_interval` (30s, a remote
  but latency-sensitive path for picking up new work). Configurable via
  `--reconcile-poll` / `Harness.run(reconcile_interval=...)`, never hardcoded (NFR).
  **Candidate selection is least-recently-checked** (2.1's `checkedAt`), settling how
  "one candidate per tick" (spec's proposed default) avoids starving later candidates
  — not spelled out in the spec, resolved in 1.6.

## 4. Flow walkthrough

**Happy path**: landing opens PR #45 → `task.data["pr"]` set, task dispatched to `end`
→ dispatcher moves it into `done` → some time later a human merges #45 on GitHub → next
`_reconcile_loop` tick: `MergeReconciler` finds the task in `done` (has `data.pr`,
`checkedAt` unset so it sorts first), claims it, `GithubMergeChecker.is_merged` returns
`True`, task transfers `done → archived` with a new history entry, `"archived"` event
fires → `ProjectionSink` calls `BoardProjection.archive(task)` → next SSE frame → the
board's `done` column no longer lists it, `GET /api/tasks/{id}` still resolves it.

**Crash between claim and transfer**: process dies after `MergeReconciler` claims the
task out of `done` (file now in `done/.processing/`) but before `transfer()` completes.
On restart, `Harness.recover()` now includes `done` in its recovered-queue list, so the
file moves back from `done/.processing/` to `done/`, `lock_id` cleared — indistinguishable
from a task that was never claimed. `hydrate()` picks it up into the `done` column as
usual. The next reconcile tick claims and checks it again — no bespoke recovery code,
exactly as the spec's crash-safety NFR requires.

**No PR / fake forge**: a task with no `data.pr` (pre-feature history, or `harness
submit` tasks that never land) is never a candidate — `done.list()` filters on
`isinstance(t.data.get("pr"), dict)`, so it is never claimed, never inspected, never
errors, and stays in `done` forever exactly as today. A task landed through `FakeForge`/
`MemoryForge` gets a `data.pr` block with a non-GitHub `repo` placeholder, but since
`cli.py` only wires a `merge_checker` when `GITHUB_TOKEN` is present (same gate as
`GithubForge`), such tasks are simply never fed to a live reconciler in practice.
