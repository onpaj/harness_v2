# Remove a task from the dashboard once its PR is closed

## Summary

Today a task that reaches `end` sits in the `done` column of the board forever,
even after the pull request `land` opened for it has long since been merged or
closed unmerged on GitHub. This adds a background watcher that checks a landed
task's pull request, and once it is resolved — merged or closed without
merging — takes the task off the board. The task's record and history are kept
on disk (for audit / direct lookup), only its presence on the board goes away.

## Context

`land` (`behaviors/landing.py`) opens a PR and returns `done`; the dispatcher
then moves the task to `end`/`done` (`dispatcher.py:_finish`) and it stays
there permanently — nothing in the harness today looks at the PR again. The
board is an operator's view of *active* work; a task whose PR was merged or
closed days ago is no longer active, it's just accumulating in `done` and
diluting the view. GitHub already auto-closes a linked issue on merge (via the
`Closes #n` in the PR body, `github_forge.py:_body`), but nothing analogous
exists for the harness's own board — this closes that gap. This is a natural
extension of the existing "outside world" pattern (`SourcePoller`,
`SourceReflectorSink`) applied to the forge instead of the source: poll an
external signal on an interval, react by moving the task, never block the
orchestration loop on it.

## Functional requirements

**FR-1 — Query a task's PR state, any state, distinguishing merged from closed-unmerged.**
`Forge` (today: `open_pull_request` only) gains a way to ask "what happened to
this task's PR": still open, merged, or closed without merging.
- AC: for a task whose PR is open, the query reports "open".
- AC: for a task whose PR was merged, the query reports "merged".
- AC: for a task whose PR was closed without merging, the query reports "closed" (distinct from "merged").
- AC: `GithubForge`, `FakeForge` and `MemoryForge` all implement it (every driver behind the port, matching how `open_pull_request` is implemented by all three today).

**FR-2 — A landed task remembers enough about its own PR to be checked later.**
The watcher must be able to re-identify the PR without re-deriving it from
git state (no re-attaching the worktree just to read a branch name).
- AC: after `land` succeeds, the task carries its PR reference (at minimum the branch/number used to open it) in `task.data`.
- AC: a task landed before this feature shipped (no stored PR reference) is simply never picked up by the watcher — no crash, no retry storm.

**FR-3 — Periodically check landed tasks for a resolved PR.**
A new component, modeled on `SourcePoller`, ticks on its own interval,
independent of the fast dispatcher/consumer poll loop.
- AC: on each tick, every task currently in the `done` queue with a stored PR reference is checked via FR-1.
- AC: a task whose PR is still open is left untouched (checked again next tick).
- AC: a forge/network failure during a tick is caught and logged as an event; it does not crash the harness and does not stop other tasks in that tick from being checked (mirrors `SourcePoller.tick`'s handling of a `poll()` failure).

**FR-4 — A resolved task leaves the board.**
Once FR-3 finds a task's PR merged or closed:
- AC: the task no longer appears in any column of `BoardView.snapshot()`.
- AC: the task remains retrievable by id (`BoardView.get(task_id)`) and its artifacts/history stay intact — only board *listing* changes, nothing is deleted.
- AC: this happens for both outcomes (merged, closed-unmerged) — the task leaves the board either way.
- AC: the removal is reflected live over `BoardView.subscribe()` (a revision bump), the same way every other board change is.

**FR-5 — Audit trail of the resolution.**
- AC: a `HistoryEntry` is appended to the task recording that it was archived and why (merged vs. closed-unmerged), with an actor identifying the watcher (parallel to `actor="dispatcher"`/`actor="operator"`).
- AC: the underlying task file survives on disk after this (in a new terminal location, not deleted), the same way `done/` and `failed/` already preserve resolved tasks.

**FR-6 — Configurable, off by default from breaking existing runs.**
- AC: `harness run` gains a flag for the watcher's poll interval, following the existing convention (`--poll`, `--source-poll`).
- AC: an empty `done` queue (nothing to check) is a cheap no-op tick, matching `SourcePoller.tick()`'s empty case.

## Non-functional requirements

- **No busy-looping GitHub.** The watcher's interval is independent of and coarser than the dispatcher/consumer's `--poll` (same reasoning as `--source-poll` already documented in `cli.py`: a remote API has rate limits, local queues don't).
- **Never blocks a queue.** This must not be modeled as a normal workflow step whose `Consumer` claims a task and blocks on it — a PR can take days to close, and a single claimed-and-blocked task would stall every other task behind it in that step's queue. It is a periodic scan over already-terminal tasks, not a claim/consume cycle.
- **Failure isolation.** A GitHub outage degrades to "the board stops shrinking," never to "the harness stops running" — same posture as `SourcePoller` and `CompositeEventSink` today.
- **No new invariant violations.** `dispatcher.py`/`consumer.py` must not import the forge port or the new archive queue — the watcher and its wiring live in `app.py`/a new core module, exactly like `SourcePoller`/`source_poller.py` today. `tests/test_architecture.py` already guards the shape this must take (see `WORK_PORTS`, `test_orchestration_does_not_import_drivers`).

## Data model

- **`PullRequestState`** (new): `OPEN | MERGED | CLOSED` — the tri-state result of FR-1's query. `CLOSED` means "closed, not merged."
- **`Task.data["pr"]`** (new, written by `LandingBehavior` on success): `{"number": int, "url": str, "branch": str}` — the minimum needed to re-query the PR later without touching git. This requires `BehaviorResult` (currently `outcome` + `summary`) to gain an optional third field the consumer merges into `task.data` on delivery — a small, general extension (useful beyond this feature), not something landing does by reaching around the consumer.
- **A third terminal queue, `archived/`** (new), alongside the existing `done/` and `failed/` — a queue nobody consumes, exactly like those two. Tasks move `done → archived` once resolved; `archived/` is deliberately **not** included in `BoardProjection.hydrate()`, so archived tasks never repopulate the board even across a restart.
- **`BoardProjection`** gains an explicit "task leaves the board" path (distinct from `apply`, which stores a task under a column): the task is dropped from the columns index but kept queryable by id (per FR-4's AC), which is a small, deliberate change to `projection.py` — not a side effect of routing to an unlisted column name.
- **New event kind `"archived"`** (parallel to `"finished"`/`"failed"` in `dispatcher.py`, `"restarted"` in `task_control.py`): carries `task_id` and the resolution (`merged`/`closed`). `ProjectionSink` and `SourceReflectorSink` each get a small addition to react to it (or explicitly ignore it, for the reflector — see Open Questions).

## Interfaces

- `Forge.pull_request_state(task: Task) -> PullRequestState` — new abstract method, implemented by `GithubForge`, `FakeForge`, `MemoryForge`.
- `GithubClient` needs a way to fetch a PR regardless of state and tell merged from closed-unmerged (today `find_pull_request` hardcodes `state=open`); `FakeGithubClient` needs a test-facing way to simulate a PR closing/merging.
- New core component, e.g. `PrWatcher.tick() -> bool`, constructed with `{done queue, archived queue, forge, events, clock}` — same shape as `SourcePoller(source, inbox, events)`.
- `Harness.run()` gains a fourth loop, `_pr_watcher_loop`, alongside `_dispatcher_loop`/`_consumer_loop`/`_source_loop`, on its own interval.
- `harness run --pr-poll <seconds>` (default TBD, see Open Questions) analogous to `--source-poll`.
- No UI/API surface changes are required beyond the existing `BoardView`/board websocket — the dashboard already re-renders from `snapshot()`/`subscribe()`, so a task disappearing from a column is "free" once `BoardProjection` stops listing it.

## Dependencies and scope

**Depends on:** `Forge`/`GithubForge`/`FakeForge`/`MemoryForge`, `GithubClient`/`FakeGithubClient`/`HttpGithubClient`, `BoardProjection`/`ProjectionSink`, `Harness.run()`'s loop structure, `SourcePoller` as the architectural template, `LandingBehavior`.

**In scope:**
- Querying and distinguishing merged vs. closed-unmerged PR state.
- Persisting a PR reference on a landed task.
- The periodic watcher and its wiring (`app.py`, `cli.py`).
- Board-side removal (`BoardProjection`, `ProjectionSink`).
- Tests: unit coverage for the state query, the watcher's tick, and board archive/removal semantics; extending `tests/test_smoke_git.py` (real git + fake forge) to close a PR and assert it drops off the board.

**Out of scope:**
- Any change to how the source GitHub *issue* is labeled once the PR resolves. Merge already auto-closes the issue via `Closes #n`; a PR closed unmerged leaving the issue open/labeled `harness:pr-open` is unchanged by this work (flagged below as an open question, not assumed).
- Webhooks / push-based notification — this stays a poll, like every other outside-world check in the harness today.
- Retroactively archiving tasks already sitting in `done/` before this ships (they have no stored PR reference — see FR-2's second AC).
- A UI affordance to un-archive / restore a task to the board.
- Rate-limit backoff, multi-process coordination — same "not yet" as `SourcePoller` (phase 4 spec, "What's still out of scope").

## Rough plan

1. Extend `GithubClient` (+ `FakeGithubClient`, `HttpGithubClient`) to fetch a PR's state regardless of open/closed, and tell merged from closed-unmerged; give `FakeGithubClient` a test helper to simulate a close/merge.
2. Add `PullRequestState` and `Forge.pull_request_state(task)`; implement in `GithubForge` (real API), `FakeForge` (reads/writes `prs.json`, needs a test-facing "close" helper too), `MemoryForge`.
3. Extend `BehaviorResult` with an optional `data` payload; `Consumer._deliver` merges it into `task.data`. `LandingBehavior` attaches `{"pr": {...}}` on success.
4. Add the `archived/` queue to `HarnessLayout` and `build()`, alongside `done`/`failed`.
5. Add the `PrWatcher` core component (`pr_watcher.py`, ports-only) with a `tick()`: scan `done`, skip tasks with no `data["pr"]`, query FR-1, on resolution — append history, transfer `done → archived`, emit `"archived"`.
6. Extend `BoardProjection` with the "leave the board, stay gettable by id" removal path; extend `ProjectionSink` to handle `"archived"`.
7. Wire `PrWatcher` into `build()`/`Harness` (new loop in `run()`), add `--pr-poll` to `harness run` in `cli.py`.
8. Tests: unit tests for the state query (open/merged/closed), the watcher's tick (resolves → archives, still-open → untouched, forge error → isolated), `BoardProjection`'s new removal path; extend `tests/test_smoke_git.py` end-to-end.
9. Confirm `tests/test_architecture.py` still passes unmodified (the new port/queue must not be reachable from `dispatcher.py`/`consumer.py`) — add a guard entry only if a new import boundary needs one.

## Open questions

- **Poll interval default.** Proposing 300s (5 min) — coarser than `--source-poll`'s 30s default since a merge is rarely urgent to reflect within seconds, but far more frequent than a human checks the dashboard. Flag if a different default is wanted.
- **Should a PR closed-unmerged also relabel/reopen anything on the source issue?** Today only merge implicitly resolves the issue (via `Closes #n`). A closed-unmerged PR currently leaves the issue exactly as `land` left it (`harness:pr-open`), with no further signal that the work didn't land. Proposing this stays out of scope for this task (it's about the *harness's own board*, not GitHub issue bookkeeping) — call it out if the issue-side signal matters too.
- **Is "gone but still fetchable by id" the right shape for FR-4?** The alternative is deleting the task outright once archived. Proposing "off the board, still fetchable" as the default — it's non-destructive and mirrors how `failed/` already keeps a task's full history around. Flag if literal deletion (or a dedicated "archived" board column instead of full removal) is actually what's wanted.
- **Tasks landed before this ships.** They have no `data["pr"]` and will never be picked up (FR-2's second AC treats this as a non-error). Accepting them as a permanent, harmless residue in `done/` unless a backfill is explicitly requested.
