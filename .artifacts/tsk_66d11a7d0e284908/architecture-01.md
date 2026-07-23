# Architecture: Poll for merged PRs and remove their completed tasks from the dashboard

Reviewed against `plan-01.md` (spec) and `design-01.md` (component design), and against
the current source (`models.py`, `consumer.py`, `dispatcher.py`, `source_poller.py`,
`projection.py`, `app.py`, `cli.py`, `ports/{queue,forge,board}.py`,
`drivers/{fs_queue,github_client,github_forge,fake_forge,memory,projection_events}.py`,
`tests/test_architecture.py`). The design is sound and closely grounded — I confirm its
four settled decisions (dedicated `MergeChecker` port, `archived/` as a real `TaskQueue`,
indefinite retention, 300s interval with least-recently-checked selection) without
change. This document adds one concrete correction the design missed (a projection
freshness gap), pins the implementation order, and calls out the risks worth watching
before development starts.

## 1. Alignment with existing patterns and integration points

This feature is a second instance of a pattern the codebase already has exactly once:
**`SourcePoller`/`TaskSource`/`SourceReflectorSink`** (inbound: pull work in from GitHub)
gets a structural twin, **`MergeReconciler`/`MergeChecker`** (outbound: pull merge status
back in and archive). Every integration point the design proposes has a direct precedent
already in the tree:

| New piece | Mirrors | Verified against |
|---|---|---|
| `ports/merge.py::MergeChecker` | `ports/source.py::TaskSource`, `ports/control.py::TaskControl` (single-verb read/write ports) | `ports/board.py` (`BoardView`) vs `ports/control.py` split |
| `merge_reconciler.py::MergeReconciler` | `source_poller.py::SourcePoller` — core, ports+models only, `tick() -> bool`, same "catch, emit an `_error` event, return `False`" shape | `source_poller.py` lines 52–90 |
| `drivers/github_merge_checker.py` | `drivers/github_source.py` — a driver behind a dedicated port, GitHub-specific | `drivers/github_forge.py`, `drivers/github_client.py` |
| `Harness._reconcile_loop` | `Harness._source_loop` (own interval, decoupled from the tight local `poll_interval`) | `app.py` lines 190–197 |
| `archived/` `TaskQueue` | `done/`/`failed/` — "terminal states are simply queues that nobody consumes" | `ports/queue.py` docstring, `app.py` lines 249–250 |
| `BoardProjection.archive()` | The existing `_store`/`apply` split; `_store` already silently **drops** any column not in `self._order` (`projection.py:120`) — routing `"archived"` through `apply()` unchanged would silently lose the task from `_tasks` too, not just `_columns`. The design's separate `archive()` method (write to `_tasks`, pop from `_columns`) is the only correct option, not a stylistic choice. | `projection.py` lines 84–123 |
| `cli.py::_build_merge_checker` | `cli.py::_build_forge` — same `GITHUB_TOKEN` gate | `cli.py` lines 634–646 |

This confirms the plan's/design's framing is right: **this is a driver-and-a-port swap
exercise, not new architecture.** No change to `router.py`, `Workflow`, `Outcome`, or the
dispatcher's decision logic. Invariant 4 (router is pure), invariant 2/3 (decision-making
split), and invariant 12 (landing is a step, not magic) are all untouched.

Two invariants need a new instance:

- **Invariant 18/20's shape, applied to `MergeChecker`**: *"`MergeChecker` is touched only
  by `MergeReconciler` (core) and `app.py`/`cli.py` (wiring) — `dispatcher.py`/
  `consumer.py` never import `harness.ports.merge`."* This must become a real
  `test_architecture.py` assertion (§3 of the plan's rough plan step 7), not just a design
  note — it's exactly the shape `test_orchestration_does_not_import_source_port` already
  checks for `ports.source`.
- **Invariant 6's shape ("recover() before hydrate()"), applied to `done`**: `recover()`
  today only recovers `[inbox, *step_queues.values()]` (`app.py:122`) — `done`/`failed`
  are never in that list because nothing has ever claimed out of them. This changes as
  soon as `MergeReconciler` exists. `recover()` must add `self._done` to that list
  **unconditionally** (not only when a reconciler is wired) — a `.processing/` file can
  outlive the run that created it, and recovering an idle queue is a no-op. This is
  already correctly identified in `design-01.md` §1.7; I'm elevating it here because it's
  easy to gate on "only if reconciler is not None" by mistake, which would silently break
  crash recovery for exactly the case this feature is *for*.

## 2. Proposed architecture

### 2.1 Components (all new except the two extended in place)

```
ports/merge.py            MergeChecker(ABC) — is_merged(task) -> bool | None
drivers/github_merge_checker.py   GithubMergeChecker(MergeChecker)
drivers/github_client.py  + GithubClient.get_pull_request, PullRequestDetail   (extended)
drivers/memory.py         + FakeMergeChecker                                   (extended)
merge_reconciler.py       MergeReconciler — core, tick() -> bool
ports/forge.py            PullRequest.repo: str                                (extended)
models.py                 BehaviorResult.data: dict | None = None              (extended)
consumer.py                _deliver merges result.data into task.data          (extended)
behaviors/landing.py      builds task.data["pr"] from the returned PullRequest  (extended)
projection.py             BoardProjection.archive(task)                        (extended)
drivers/projection_events.py  ProjectionSink routes "archived" -> archive()     (extended)
app.py                    HarnessLayout.archived; Harness.archived/.reconciler;
                           build(merge_checker=...); recover() includes done;
                           run(reconcile_interval=300.0) + _reconcile_loop       (extended)
cli.py                    _build_merge_checker; --reconcile-poll flag           (extended)
```

### 2.2 Key decisions

**Decision 1 — `MergeChecker` is a dedicated port, not a `Forge` method. Confirmed.**
Considered: adding `Forge.is_merged(task)`. Rejected — `Forge` is deliberately the *write*
side of landing only ("the harness never touches `main` — it only proposes"; "the merge
strategy is a human's call"). A read capability with an entirely different failure
contract (must distinguish "not merged" from "couldn't check", §FR-2) belongs on its own
port, exactly the way `BoardView` (read) and `TaskControl` (write) are already split for
the board. This also keeps `FakeForge`/`MemoryForge` untouched by the new capability —
they gain a `repo` field (decision 5) but never need to answer "is it merged".

**Decision 2 — `archived/` is a real `TaskQueue`, not a flag on `Task`. Confirmed.**
Considered: `Task.data["archivedAt"]` with the projection filtering `done` tasks that
carry it. Rejected — that reintroduces exactly the bespoke recovery logic invariant 6 and
`ports/queue.py`'s docstring exist to avoid: a flag-based approach needs its own crash-
safety story (what if the process dies after setting the flag but before some other side
effect?), where a queue gets `claim`/`transfer`/`recover` for free, identically to how
`done`/`failed` already work. `archived/` is also cheap: it's constructed in `build()`
only when a `merge_checker` is supplied, so a run without GitHub wiring pays nothing.

**Decision 3 — Selection is least-recently-checked, one candidate per tick. Confirmed.**
The design's `checkedAt` stamp (`task.data["pr"]["checkedAt"]`, sort ascending, unset
first) is the right fix for the starvation bug it identifies: naive "first in
`done.list()` order" would wedge on the first unmerged task forever. This is the one
place in the feature that isn't a straight port of an existing pattern — `SourcePoller`
has no analogous "keep cycling through open items" requirement, because it processes
*all* of `poll()`'s results every tick rather than picking one candidate out of a
standing pool. I re-derived the starvation scenario independently (see §2.2 walkthrough
in `design-01.md` §1.6) and it holds: accept as designed, but see §3 below for a gap in
how the checked timestamp is surfaced.

**Decision 4 — 300s reconcile interval, configurable, never hardcoded elsewhere.
Confirmed**, and distinctly *not* reusing `source_interval`'s 30s: `source_interval` is a
latency-sensitive "pick up new work" path, this is a housekeeping sweep. Threading it
through `--reconcile-poll` the same way `--source-poll` already threads into
`source_interval` (`cli.py:687,693-701`) is the right amount of consistency — don't invent
a second flag-naming convention.

**Decision 5 — `BehaviorResult.data` is a generic, optional, additive field. Confirmed,
with a scope constraint.** This is the one piece of this feature that changes a
core contract (`BehaviorResult`, today `outcome`+`summary` only) rather than adding a new
file behind a port. It's safe *mechanically* — default `None`, shallow-merged only when
present, both existing call sites (`dummy_behavior.py:72`, `behaviors/agent.py:80`) pass
positionally and are unaffected — and it doesn't create a branch on `outcome` in
`consumer.py`, so invariant 2's guard (`test_consumer_has_no_branch_on_outcome_value`)
stays satisfied by construction: the merge is unconditional data plumbing, not a decision.
**Scope it deliberately**: `data` exists to let a behavior attach structured facts about
*what it produced* (here: PR identity) — it is not a general escape hatch for a behavior
to reach into `task.data` and mutate arbitrary keys. Only `LandingBehavior` should
populate it in this feature; a future step reusing it for something unrelated to its own
output is a smell worth catching in review.

## 3. Implementation guidance

### 3.1 A correction to `design-01.md` §1.6: `_release` must emit a task-movement event

I traced every `TaskQueue.transfer` call site (`dispatcher.py:80-132`,
`consumer.py:86-127`) and every one is followed by `self._events.emit(name, ...,
queue=<destination.name>, task=<dict>)` — that's what feeds `ProjectionSink.apply()`
(`drivers/projection_events.py:16-27`), which unconditionally overwrites
`self._tasks[task.id]` (`projection.py:84-87`) regardless of whether the column changed.
`FilesystemTaskQueue.transfer` itself never emits anything (`fs_queue.py:81-88`) — it's a
pure file move; the caller is responsible for telling the projection.

`design-01.md`'s `MergeReconciler._release` (the "not merged, release back into `done`"
and "checker raised, release back into `done`" paths) calls
`self._done.transfer(released, self._done)` but **emits no event**. That means the
`checkedAt` stamp written to disk (the mechanism decision 3 depends on to avoid
starvation) never reaches `BoardProjection`'s in-memory copy of the task — `GET
/api/tasks/{id}` would keep showing the pre-check `data.pr` until some unrelated event
happens to touch that task again. The archival *decision* itself is unaffected (the next
`tick()` re-reads `checkedAt` from disk via `self._done.list()`, not from the
projection), so this is a freshness bug in the read model, not a correctness bug in the
reconciler — but it's a real gap and cheap to close. Fix: `_release` should emit the same
shape every other transfer emits:

```python
def _release(self, task: Task, *, checked_at: str | None) -> None:
    pr = dict(task.data["pr"])
    if checked_at is not None:
        pr["checkedAt"] = checked_at
    released = replace(task, lock_id=None, data={**task.data, "pr": pr})
    self._done.transfer(released, self._done)
    self._events.emit("rechecked", task_id=released.id, queue=self._done.name, task=released.to_dict())
```

Because `queue="done"` (not `"archived"`), this needs **no new routing branch** in
`ProjectionSink` — it flows through the existing `apply("done", task)` path unchanged.
Only the `"archived"` event name needs the new `archive()` routing branch the design
already specifies. Name it something distinct from `"claimed"`/`"consumed"` (I suggest
`"rechecked"`) so it's legible in the event stream and in any future audit of what the
reconciler actually did on a given tick.

### 3.2 Interfaces and contracts (as specified in `design-01.md`, confirmed)

```python
# ports/merge.py
class MergeChecker(ABC):
    def is_merged(self, task: Task) -> bool | None: ...
    # True: merged. False: open. None: task carries no task.data["pr"]. Raises: transient
    # failure — caller must retry, never treat as "not merged".

# merge_reconciler.py — core, imports only harness.models and harness.ports.*
class MergeReconciler:
    def __init__(self, *, done: TaskQueue, archived: TaskQueue, checker: MergeChecker,
                 events: EventSink, clock: Clock) -> None: ...
    def tick(self) -> bool: ...  # True only on an actual archive (mirrors SourcePoller's
                                  # "True means something changed" contract, not "True
                                  # means we did work" — see design-01.md §1.6 for why a
                                  # tight sleep(0) on every "examined, still open" would
                                  # defeat the whole point of backing off from GitHub).
```

`GithubClient.get_pull_request` and `PullRequestDetail` (design §1.4) extend the existing
`ABC` the same way every other verb was added — `FakeGithubClient` and `HttpGithubClient`
both implement it, `FakeGithubClient` gains a `merge_pull_request(number)` test helper
mirroring the shape of its existing `add_label`/`remove_label`.

### 3.3 Data flow

```
LandingBehavior.run()
  -> Forge.open_pull_request() -> PullRequest(..., repo=slug)
  -> BehaviorResult(DONE, "opened PR ...", data={"pr": {...}})
       |
       v
Consumer._deliver()  — merges result.data into task.data, no branch on outcome
       |
       v
Dispatcher  — routes end -> `done` queue (unchanged; dispatcher never reads data.pr)
       |
       v
(task sits in done/, data.pr set, no checkedAt)
       |
       v  every reconcile_interval (300s)
MergeReconciler.tick()
  -> done.list() filtered to isinstance(data.get("pr"), dict)
  -> sort by (checkedAt or "", created, id) ascending  — least-recently-checked first
  -> done.claim(candidate)
  -> MergeChecker.is_merged(task)
       - raises        -> _release(checked_at=None), emit "rechecked", emit "merge_check_error"
       - False          -> _release(checked_at=now),  emit "rechecked"
       - True           -> transfer done -> archived, append HistoryEntry, emit "archived"
       |
       v
ProjectionSink
  - "rechecked" (queue="done")     -> BoardProjection.apply("done", task)   [existing path]
  - "archived"  (queue="archived") -> BoardProjection.archive(task)         [new path]
       |
       v
BoardView.snapshot() — archived task absent from every column
BoardView.get(id)    — archived task still resolves, with full history + data.pr
```

### 3.4 Where new code belongs (confirms the plan's rough-plan ordering; this is the build order)

1. `models.py::BehaviorResult.data` + `consumer.py::_deliver` merge — foundational,
   nothing downstream compiles without it. Extend `tests/test_consumer.py`.
2. `ports/forge.py::PullRequest.repo` (required field) + the three implementations
   (`GithubForge`, `FakeForge`, `MemoryForge`) + `LandingBehavior` building
   `task.data["pr"]`. **Verified low blast radius**: `PullRequest(` is constructed in
   exactly three files (`drivers/{memory,github_forge,fake_forge}.py`); no test in the
   suite constructs `PullRequest` directly (`tests/test_landing.py`,
   `tests/test_github_forge.py` don't reference the type at all), so this is a contained,
   mechanical change — not the wide-blast-radius risk a required-field addition usually
   is.
3. `ports/merge.py` + `GithubClient.get_pull_request`/`PullRequestDetail` on both
   `FakeGithubClient`/`HttpGithubClient` + `GithubMergeChecker` + `FakeMergeChecker` (in
   `drivers/memory.py`, next to `MemoryForge`).
4. `merge_reconciler.py` (apply the §3.1 fix here) + unit tests mirroring
   `tests/test_source_poller.py`'s shape (seed, tick, error path, starvation-avoidance
   round-robin, crash-then-recover).
5. `HarnessLayout.archived` + `app.py` wiring (`archived` queue only constructed when
   `merge_checker` is supplied; `Harness.recover()` adds `done` **unconditionally**, per
   §1; `run(reconcile_interval=300.0)` + `_reconcile_loop`; `projection.hydrate(...,
   archived=...)`).
6. `BoardProjection.archive()` + `ProjectionSink` routing for `"archived"` (and the
   `"rechecked"` event flowing through the existing `apply()` branch, no code change
   needed there beyond emitting it from step 4). Board/API tests confirming an archived
   task is gone from `/api/board` but `GET /api/tasks/{id}` still resolves it.
7. `cli.py::_build_merge_checker` + `--reconcile-poll` + extend `test_architecture.py`
   with the `MergeChecker`-isolation assertion from §1 + an in-memory e2e test (land →
   mark merged in the fake → tick → assert gone from `done`/board, present at `get()`).
8. Update `CLAUDE.md`: module map (`ports/merge`, `drivers/github_merge_checker`,
   `merge_reconciler.py`), and a new numbered invariant for the `MergeChecker` isolation
   rule (§1) — the next free number is **24** (invariant 23 is the last one currently in
   the file).

## 4. Risks and mitigations

- **Projection freshness gap (the `_release` event).** Addressed directly in §3.1 — treat
  it as part of the design, not an optional polish item, since it's the one place this
  feature would otherwise silently diverge from the codebase's "every transfer emits"
  convention.
- **`BehaviorResult.data` scope creep.** A generic bag on the behavior→task boundary is
  easy to reach for again later for unrelated purposes, gradually turning `task.data` into
  an untyped grab-bag with no single owner. Mitigation: this feature only ever writes
  `data.pr` from `LandingBehavior`; no other behavior in this codebase needs `data` today.
  Flag in review if a later change starts writing a second, unrelated key from a different
  behavior — that's a sign that a real port/method is missing.
- **`recover()` gated on the reconciler being enabled.** If `done` is only added to
  `recover()`'s queue list conditionally on `self.reconciler is not None`, a task
  claimed-but-not-archived under a *previous* run (reconciler was on, then the operator
  disabled it) is stuck in `done/.processing/` forever with no path back. Mitigation:
  make it unconditional, as specified in §1 and `design-01.md` §1.7 — it's a no-op on an
  idle queue, so there is no cost to always including it.
- **`GithubMergeChecker.is_merged` on a non-GitHub `repo` placeholder.** `FakeForge`/
  `MemoryForge` synthesize `repo` strings (`"local/<branch>"`, `"memory/<branch>"`) that
  are not real slugs. This is safe *only* because `cli.py` never wires a live
  `merge_checker` alongside a fake forge (§FR-2 AC, confirmed in `design-01.md` §4). This
  coupling is implicit, not enforced by any test — worth a one-line comment at the
  `_build_merge_checker` call site in `cli.py` making the dependency explicit, and a test
  asserting `_build_merge_checker` returns `None` whenever `GITHUB_TOKEN` is unset,
  independent of `--forge`.
- **Rate limits / API cost.** Bounded by construction: at most one `get_pull_request` call
  per `reconcile_interval` (one candidate per tick), default 300s. No batching API is used
  (GitHub has no "check many PRs" endpoint cheaper than N calls anyway), so this doesn't
  need a follow-up unless the number of simultaneously outstanding `done` tasks with
  `data.pr` becomes large (hundreds) — not a realistic scale for this project today.
- **Required-field addition to `PullRequest`.** Normally a risk (breaks every existing
  construction site silently at the type level, loudly at runtime). Verified contained to
  three driver files with no direct-construction test dependence (§3.4 step 2) — proceed
  as designed, but re-run the full suite after this specific step before continuing, since
  it's the one change in the whole feature with blast radius outside the new files.

## 5. Prerequisites before implementation begins

1. Confirm invariant numbering: this document assumes **24** is free (invariant 23 is
   `TaskControl`/`restart`, the most recent addition). Re-check `CLAUDE.md` immediately
   before writing the invariant text in case another task landed one in the meantime.
2. No external prerequisite — this builds entirely on already-shipped phase 3/4
   infrastructure (`GithubForge`, `RepositoryRegistry`, `GithubClient`,
   poller/reflector). No new production dependency (`get_pull_request` stays on stdlib
   `urllib`, per the existing `github_client.py` module-level constraint).
3. Implementation must follow the §3.4 order — step 2 (`PullRequest.repo`) is a breaking
   change to a shared type and should land (and be tested green) before step 3 starts
   consuming it, not interleaved.
