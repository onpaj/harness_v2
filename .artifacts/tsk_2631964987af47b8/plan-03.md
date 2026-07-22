# Remove a task from the dashboard once its PR is closed

> This is a fresh planning pass for a **second** retried attempt at this task
> (the `development` step timed out again before writing any code — same
> failure mode as the first retry, nothing in the worktree needed undoing,
> and nothing about the plan/design/architecture was at fault either time).
> Before writing this I re-verified against current source, not against the
> prior artifacts' claims: `git log --oneline -- models.py ports/forge.py
> consumer.py dispatcher.py projection.py drivers/projection_events.py
> source_poller.py app.py cli.py drivers/fs_queue.py drivers/fake_forge.py
> drivers/memory.py drivers/github_client.py drivers/github_forge.py
> behaviors/landing.py task_control.py router.py tests/test_architecture.py`
> still stops at `0e6f5d8` — the same commit `architecture-02.md` checked
> against — and `git status` is clean. I re-read the two "verified bugs"
> directly: `src/harness/projection.py:96-98` still does the unconditional
> `self._columns[task_id] == name`, `src/harness/app.py:121-122` still
> recovers `[self._inbox, *self._step_queues.values()]` with `self._done`
> absent, and `tests/test_architecture.py:89-100`
> (`test_source_poller_imports_only_ports_and_models`) is still the per-file
> pattern to mirror for `pr_watcher.py`. Nothing has drifted across two
> retries now. This document supersedes `plan-02.md` for downstream steps; it
> restates the plan in full rather than pointing back at it, and carries
> forward every decision `architecture-01.md`/`architecture-02.md` already
> validated against the source, so this retry doesn't re-litigate settled
> questions a third time.

## Summary

Today a task that reaches `end` sits in the `done` column of the board
forever, even after the pull request `land` opened for it has long since been
merged or closed unmerged on GitHub. This adds a background watcher that
checks a landed task's pull request, and once it is resolved — merged or
closed without merging — takes the task off the board. The task's record and
history are kept on disk (for audit / direct lookup); only its presence on
the board goes away.

## Context

`land` (`behaviors/landing.py`) opens a PR and returns `done`; the dispatcher
then moves the task to `end`/`done` (`dispatcher.py:_finish`) and it stays
there permanently — nothing in the harness today looks at the PR again. The
board is an operator's view of *active* work; a task whose PR was merged or
closed days ago is no longer active, it's just accumulating in `done` and
diluting the view. GitHub already auto-closes a linked issue on merge (via
the `Closes #n` in the PR body, `github_forge.py:_body`), but nothing
analogous exists for the harness's own board — this closes that gap. This is
a natural extension of the existing "outside world" pattern (`SourcePoller`,
`SourceReflectorSink`) applied to the forge instead of the source: poll an
external signal on an interval, react by moving the task, never block the
orchestration loop on it.

The originating issue's "Notes" section floats two design axes and
explicitly leaves them open. This plan resolves both, matching what two
independent prior design/architecture passes already settled on (see Open
questions, which records these as **decided**, not merely proposed):

- **Poll the PR state**, not push-based signaling — a new `PrWatcher`
  component, modeled directly on `SourcePoller`.
- **Hidden/archived, not deleted** — a new terminal queue (`archived/`) that
  `BoardProjection` excludes from listings but still serves by id.

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
- AC: a forge/network failure while checking one task is caught and logged as an event; it does not crash the harness and does not stop other tasks in that same tick from being checked (mirrors `SourcePoller.tick`'s handling of a `poll()` failure, but isolated per-task rather than per-tick, since `pull_request_state` is one call per task, not one call for the whole batch).

**FR-4 — A resolved task leaves the board.**
Once FR-3 finds a task's PR merged or closed:
- AC: the task no longer appears in any column of `BoardView.snapshot()`.
- AC: the task remains retrievable by id (`BoardView.get(task_id)`) and its artifacts/history stay intact — only board *listing* changes, nothing is deleted.
- AC: this happens for both outcomes (merged, closed-unmerged) — the task leaves the board either way.
- AC: the removal is reflected live over `BoardView.subscribe()` (a revision bump), the same way every other board change is.
- AC: the removal survives a harness restart — a task archived in a previous run is still excluded from every column and still `get()`-able after `hydrate()` runs again (this needs an explicit `archived` hydration path; the plan's data model spells it out below so it isn't left for development to notice on its own).

**FR-5 — Audit trail of the resolution.**
- AC: a `HistoryEntry` is appended to the task recording that it was archived and why (merged vs. closed-unmerged), with an actor identifying the watcher (parallel to `actor="dispatcher"`/`actor="operator"`).
- AC: the underlying task file survives on disk after this (in a new terminal location, not deleted), the same way `done/` and `failed/` already preserve resolved tasks.

**FR-6 — Configurable, off by default from breaking existing runs.**
- AC: `harness run` gains a flag for the watcher's poll interval, following the existing convention (`--poll`, `--source-poll`, `--api-port 0`=disabled).
- AC: an empty `done` queue (nothing to check) is a cheap no-op tick, matching `SourcePoller.tick()`'s empty case.
- AC: with the flag left at its default, an existing `harness run` invocation behaves identically to today (no watcher loop runs).

## Non-functional requirements

- **No busy-looping GitHub.** The watcher's interval is independent of and coarser than the dispatcher/consumer's `--poll` (same reasoning as `--source-poll` already documented in `cli.py`: a remote API has rate limits, local queues don't).
- **Never blocks a queue.** This must not be modeled as a normal workflow step whose `Consumer` claims a task and blocks on it — a PR can take days to close, and a single claimed-and-blocked task would stall every other task behind it in that step's queue. It is a periodic scan over already-terminal tasks, not a claim/consume cycle.
- **Failure isolation.** A GitHub outage degrades to "the board stops shrinking," never to "the harness stops running" — same posture as `SourcePoller` and `CompositeEventSink` today.
- **Crash safety.** The watcher's own claim/transfer sequence (it must claim a task out of `done/` before moving it to `archived/`, the same atomic-rename discipline every other queue transition uses) must not silently strand a task if the process dies mid-tick. This is new: nothing today claims out of `done/`, so `Harness.recover()` has never needed to recover it — it must now (see Data model / Dependencies and scope).
- **No new invariant violations.** `dispatcher.py`/`consumer.py` must not import the forge port or the new archive queue — the watcher and its wiring live in `app.py`/a new core module, exactly like `SourcePoller`/`source_poller.py` today. `tests/test_architecture.py` already guards the shape this must take (see `WORK_PORTS`, `test_orchestration_does_not_import_drivers`) and needs a parallel per-file import-boundary guard for the new module, matching `test_source_poller_imports_only_ports_and_models` — there is no generic "every core module obeys this" check, guards are per-file today.

## Data model

- **`PullRequestState`** (new): `OPEN | MERGED | CLOSED` — the tri-state result of FR-1's query. `CLOSED` means "closed, not merged."
- **`Task.data["pr"]`** (new, written by `LandingBehavior` on success): `{"number": int, "url": str, "branch": str}` — the minimum needed to re-query the PR later without touching git. This requires `BehaviorResult` (currently `outcome` + `summary`) to gain an optional third field (`data: dict[str, Any] | None`) the consumer merges into `task.data` on delivery — a small, general extension (useful beyond this feature), not something landing does by reaching around the consumer. The merge must be unconditional (`if result.data else task.data`), not a branch on `result.outcome`'s value, so it doesn't trip `test_consumer_has_no_branch_on_outcome_value`.
- **A third terminal queue, `archived/`** (new), alongside the existing `done/` and `failed/` — a queue nobody consumes, exactly like those two. Tasks move `done → archived` once resolved.
- **`BoardProjection` gains an explicit "task leaves the board" path.** Two changes, needed together, not one:
  - a `_register`-style primitive that keeps a task queryable by id while removing it from the columns index, used both live (on the new `"archived"` event) and at startup — `hydrate()` needs an optional `archived` queue parameter that registers every already-archived task this way, otherwise a task archived in a prior run loses its `get()`-ability across a restart (this is what FR-4's last AC calls out explicitly).
  - `snapshot()`'s per-column listing currently assumes every task in `self._tasks` has a matching entry in the columns index; once a task can be present in `_tasks` without a column, that assumption must be relaxed (a lookup that tolerates "no column" rather than raising), or the very first `snapshot()` after any archive breaks the whole board, not just that one task's listing. This is called out here as a first-class part of the data model, not an incidental implementation detail — it's the one place a naive implementation of "leave the board" silently becomes an outage. **Confirmed still present verbatim** at `src/harness/projection.py:96-98` (`self._columns[task_id] == name`, unconditional) as of this plan's re-verification.
- **`Harness.recover()` must include the `done` queue.** Recovery today only walks `[inbox, *step_queues.values()]` (`src/harness/app.py:121-122`), which has been correct so far because nothing has ever claimed out of `done/` (`dispatcher._finish` only writes into it). The watcher's claim-then-transfer sequence changes that: a crash between the two would strand a task in `done/.processing/`, invisible to both `done.list()` and `BoardProjection.hydrate()` — a silent, permanent disappearance, which is a worse defect than the one this feature fixes. `recover()` must run before `hydrate()` either way (existing invariant #6), which is exactly what makes adding `done` to the recovered set safe. **Confirmed still absent** as of this plan's re-verification.
- **New event kind `"archived"`** (parallel to `"finished"`/`"failed"` in `dispatcher.py`, `"restarted"` in `task_control.py`): carries `task_id`, the resolution (`merged`/`closed`), the destination queue, and the task snapshot. `ProjectionSink` gains a name-based branch to call the projection's new removal path for this one event (every other event it handles today is a plain column move). `SourceReflectorSink` needs no change — a PR closing unmerged deliberately leaves the source issue exactly as `land` left it (see Open questions).
- **New `Task.status` value, `"archived"`.** Set on the transfer out of `done/`, purely informational — nothing routes on it (an archived task is never reintroduced to the inbox, so `router.route()`/`Dispatcher` never see it), but it makes a task fetched by id after archival self-describing.

## Interfaces

- `Forge.pull_request_state(task: Task) -> PullRequestState` — new abstract method, implemented by `GithubForge`, `FakeForge`, `MemoryForge`. Takes the whole task (not a bare PR number), consistent with how `open_pull_request` already resolves repo identity from `task.repository`/`task.worktree`; reads `task.data["pr"]["number"]` for the PR number.
- `GithubClient` needs a way to fetch a PR regardless of state and tell merged from closed-unmerged (today `find_pull_request` hardcodes `state=open`, since it exists only to make `open_pull_request` idempotent) — a new `get_pull_request(repo, *, number)` returning a small detail record (`number`, `url`, `head`, `state`, `merged`). `FakeGithubClient` needs a test-facing way to simulate a PR closing/merging (a `close_pull_request(number, *, merged)` helper).
- New core component, e.g. `PrWatcher.tick() -> bool`, constructed with `{done queue, archived queue, forge, events, clock}` — same shape as `SourcePoller(source, inbox, events)`. Lives in a new `pr_watcher.py`, same tier as `source_poller.py`: core, touches only `ports/{queue,forge,events,clock}` and `models`, never a driver.
- `Harness.run()` gains a fourth loop, `_pr_watcher_loop`, alongside `_dispatcher_loop`/`_consumer_loop`/`_source_loop`, on its own interval; only started when the interval is non-zero.
- `harness run --pr-poll <seconds>` (default `0.0` = disabled, following the `--api-port 0` convention already in this codebase — see Open questions for why `0` rather than a separate enable flag).
- No UI/API surface changes are required beyond the existing `BoardView`/board websocket — the dashboard already re-renders from `snapshot()`/`subscribe()`, so a task disappearing from a column is "free" once `BoardProjection` stops listing it.

## Dependencies and scope

**Depends on:** `Forge`/`GithubForge`/`FakeForge`/`MemoryForge`, `GithubClient`/`FakeGithubClient`/`HttpGithubClient`, `BoardProjection`/`ProjectionSink`, `Harness.run()`'s loop structure and `Harness.recover()`, `SourcePoller` as the architectural template, `LandingBehavior`, `models.BehaviorResult`/`Consumer._deliver`.

**In scope:**
- Querying and distinguishing merged vs. closed-unmerged PR state.
- Persisting a PR reference on a landed task (`BehaviorResult.data` → `task.data["pr"]`).
- The periodic watcher and its wiring (`app.py`, `cli.py`), including the `Harness.recover()` fix it requires.
- Board-side removal (`BoardProjection`, `ProjectionSink`), including the `snapshot()` fix it requires.
- A new per-file import-boundary guard test for `pr_watcher.py`, matching the existing one for `source_poller.py`.
- Tests: unit coverage for the state query, the watcher's tick (resolves→archives, still-open→untouched, forge error→isolated), board archive/removal semantics (including snapshot-after-archive and get-after-restart), and a claim-without-transfer recovery test; extending `tests/test_smoke_git.py` (real git + fake forge) to close a PR and assert it drops off the board while staying `get()`-able.

**Out of scope:**
- Any change to how the source GitHub *issue* is labeled once the PR resolves. Merge already auto-closes the issue via `Closes #n`; a PR closed unmerged leaving the issue open/labeled `harness:pr-open` is unchanged by this work.
- Webhooks / push-based notification — this stays a poll, like every other outside-world check in the harness today.
- Retroactively archiving tasks already sitting in `done/` before this ships (they have no stored PR reference, so they're permanently skipped — acceptable residue, not a defect).
- A UI affordance to un-archive / restore a task to the board.
- Rate-limit backoff, multi-process coordination — same "not yet" as `SourcePoller` (phase 4 spec, "What's still out of scope").

## Rough plan

1. Extend `GithubClient` (+ `FakeGithubClient`, `HttpGithubClient`) with `get_pull_request(repo, *, number)` to fetch a PR's state regardless of open/closed and tell merged from closed-unmerged; give `FakeGithubClient` a `close_pull_request(number, *, merged)` test helper. Unit-test the open/merged/closed mapping in isolation first.
2. Add `PullRequestState` and `Forge.pull_request_state(task)`; implement in `GithubForge` (via the new client method), `FakeForge` (reads/writes `prs.json`, extended with `state`/`merged` fields defaulting to `"open"`/`false` for pre-existing records, plus its own `close_pull_request(branch, *, merged)` helper), `MemoryForge` (an in-memory state map plus a `close(branch, *, merged)` helper).
3. Extend `BehaviorResult` with an optional `data: dict[str, Any] | None` field; `Consumer._deliver` merges it into `task.data` unconditionally (no branch on `outcome`). `LandingBehavior` attaches `{"pr": {"number", "url", "branch"}}` on success. Re-run the full suite here — this touches a shared, heavily-exercised type.
4. Add the `archived/` queue to `HarnessLayout` and `build()`, alongside `done`/`failed`; `build()` always constructs a `PrWatcher` (cheap, side-effect-free until ticked — same posture as `landing` always being built).
5. Add the `PrWatcher` core component (`pr_watcher.py`, ports-only) with `tick()`: scan `done.list()`, skip tasks with no `data["pr"]`, query FR-1, on resolution — claim, append history, set `status="archived"`, transfer `done → archived`, emit `"archived"`; a per-task try/except around the forge call emits `"pr_watch_error"` and continues. Add the import-boundary guard test in the same commit.
6. Extend `BoardProjection` with the "leave the board, stay gettable by id" removal path (`archive()` + the shared registration primitive + the `snapshot()` fix) and `hydrate()`'s new optional `archived` parameter — all together, not split across commits, since the `snapshot()` fix and the feature that exposes the bug must ship atomically. Extend `ProjectionSink` to handle `"archived"`.
7. Wire `PrWatcher` into `build()`/`Harness` (new loop in `run()`, `archived` threaded into `hydrate()`), add the `Harness.recover()` fix (include `done` in the recovered queues) in the same commit as the wiring — the bug this fixes doesn't exist until `PrWatcher` claims from `done`. Add `--pr-poll` (default `0.0`) to `harness run` in `cli.py`, threaded through `_run`/`serve` like `--source-poll`.
8. Tests: unit tests for the state query (open/merged/closed), the watcher's tick (resolves → archives, still-open → untouched, forge error → isolated), `BoardProjection`'s new removal path (archive-then-snapshot, archive-then-restart-then-get), and a recovery test simulating a crash between claim and transfer; extend `tests/test_smoke_git.py` end-to-end.
9. Run `.venv/bin/pytest -q` and confirm `tests/test_architecture.py` passes both unmodified and with the new guard test added.

## Open questions

None outstanding for this second retry — the two axes the originating issue
left open, plus the follow-on questions this plan's own data model raised,
were already worked through across **two** independent plan → design →
architecture passes on this same task (each ended in an unrelated
`development`-step timeout, not a defect in the spec) and are recorded here
as decisions, not proposals:

- **Poll vs. push:** poll, via a new `PrWatcher` modeled on `SourcePoller`. Push-based (webhooks) would need an inbound endpoint and delivery-failure handling this harness has no infrastructure for yet, and every other "outside world" check in the codebase today is a poll.
- **Hidden/archived vs. deleted:** archived — a third terminal queue (`archived/`) excluded from `BoardProjection`'s listings but still `get()`-able, matching how `failed/` already preserves a resolved task's full history. Literal deletion was considered and rejected: it forecloses a future "show archived tasks" toggle for no benefit, since "excluded from listing" already satisfies every acceptance criterion.
- **Poll interval default:** 300s (5 minutes) — coarser than `--source-poll`'s 30s default, since a merge is rarely urgent to reflect within seconds and the interval shares GitHub's API budget with the source poller in the same process.
- **Issue-side signaling on closed-unmerged:** out of scope. This task is about the harness's own board, not GitHub issue bookkeeping; deciding what (if anything) to do to the issue on an abandoned PR — reopen, relabel, comment — has no obviously-right answer and would be its own task if wanted.
- **Tasks landed before this ships:** permanent, harmless residue in `done/` — they carry no `data["pr"]` and the watcher skips them by construction. A backfill would need to re-derive a PR reference from git/GitHub history per task, which is real, separate work not justified by this task's brief.

## Note for the next `development` attempt

This is the second time `development` has timed out before writing any code,
with the spec itself unchanged and re-verified clean both times. If it times
out a third time, treat the cause as almost certainly environmental (the
`claude -p` invocation, its timeout budget, or the execution harness around
it) rather than reopening plan/design/architecture again — a third identical
planning pass would be pure overhead with nothing left to resolve.
