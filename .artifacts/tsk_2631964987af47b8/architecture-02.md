# Architecture (retry): archive a task off the board once its PR resolves

> This is a fresh architecture pass for a retried attempt at this task (the
> prior attempt's `development` step timed out on the `claude -p` call before
> writing any code — nothing about the plan, design or architecture was at
> fault, and nothing in the worktree needed to be undone). This document
> supersedes `architecture-01.md` for downstream steps.
>
> Before writing anything below I re-verified, against the current source
> (not by trusting `design-02.md`'s claim), that nothing has drifted:
> `git log --oneline -- models.py ports/forge.py consumer.py dispatcher.py
> projection.py drivers/projection_events.py source_poller.py app.py cli.py
> drivers/fs_queue.py drivers/fake_forge.py drivers/memory.py
> drivers/github_client.py drivers/github_forge.py behaviors/landing.py
> task_control.py router.py tests/test_architecture.py` stops at `0e6f5d8`,
> which predates every artifact commit this task has produced
> (`a598d25` plan-01 … `1a24ccb` design-02). `git status` is clean. I also
> re-read the two "verified bugs" directly against today's
> `src/harness/projection.py`/`src/harness/app.py`/`tests/test_architecture.py`
> rather than trusting the line numbers cited in `architecture-01.md`:
>
> - `src/harness/projection.py:96-98` (current) still does
>   `if self._columns[task_id] == name` — unconditional, unchanged.
> - `src/harness/app.py:121` (current) still does
>   `queues = [self._inbox, *self._step_queues.values()]` — `self._done` still
>   absent, unchanged.
> - `tests/test_architecture.py:89-100` still guards `source_poller.py`'s
>   import boundary *by name*, with no generic per-module check — the pattern
>   `design-02.md` §8 proposes to mirror for `pr_watcher.py` is confirmed
>   still the right shape to copy.
>
> Every conclusion `architecture-01.md` reached against `plan-01.md`/
> `design-01.md` therefore transfers unchanged to `plan-02.md`/`design-02.md`:
> same signatures, same two mandatory fixes, same resolved open questions. This
> document restates the assessment in full (per this task's own convention of
> not making downstream steps chase a superseded file) rather than pointing
> back at `architecture-01.md`, and calls out explicitly the one thing that
> changed in `design-02.md` beyond the restatement: the import-boundary guard
> test that `architecture-01.md` flagged as missing is now written into the
> design itself (§8) as a first-class component, not left as an
> architecture-only footnote. There is nothing left for this pass to add.

## Alignment with existing patterns and integration points

`PrWatcher` modeled on `SourcePoller` is the correct call, for a specific
reason beyond visual similarity: both are periodic scans of already-committed
state against an outside system (`TaskSource`/`Forge`), both must never block
orchestration on a slow or down remote, and both already have a documented
failure-isolation shape to copy (`SourcePoller.tick`'s try/except around
`poll()`, `CompositeEventSink` isolating sink failures). This is also the only
shape that satisfies FR-3's AC ("a forge failure... does not stop other tasks
in that tick from being checked") — `SourcePoller` isolates at the level of
one `poll()` call because GitHub's API is one call for the whole batch;
`PrWatcher` must isolate per-task because `pull_request_state` is one call
*per task*. The design gets this right: `pr_watch_error` is emitted per-task,
inside the loop, not once per tick.

Every touch point in `design-02.md` lands exactly where the module map says
core/ports work belongs:

- `PrWatcher` in a new `pr_watcher.py`, sibling to `source_poller.py`, touching
  only `ports/{queue,forge,events,clock}` and `models`. `source_poller.py`
  today imports nothing else — confirmed again this pass by re-reading
  `tests/test_architecture.py:89-100`, which already enforces this shape for
  its sibling. `pr_watcher.py` must satisfy the identical constraint, and
  `design-02.md` §8 now writes that guard test into the design proper.
- `Forge.pull_request_state` extends `ports/forge.py` next to
  `open_pull_request` — same ABC, same "task in, port-level exception out"
  contract.
- `BehaviorResult.data` extends `models.py`, which imports nothing from the
  package — a plain `dict[str, Any] | None`, no new import required.
- `Consumer._deliver`'s merge is an unconditional `if result.data else
  task.data`, not a branch on `result.outcome`. Checked again this pass
  against `test_consumer_has_no_branch_on_outcome_value`'s `ast`-based
  guard: an `if result.data:` check touches `result`, not `outcome`, so it
  cannot trip the guard. Confirmed safe, same conclusion as
  `architecture-01.md`.
- `BoardProjection.archive()`/`ProjectionSink`'s new branch stay inside
  `projection.py`/`drivers/projection_events.py`, which `api/` only reaches
  through `BoardView` — no new surface for `api/` to touch (invariant #5
  holds).
- `dispatcher.py`/`consumer.py` gain no new import. `PrWatcher` is wired in
  `app.py` alongside `SourcePoller`, exactly where `Forge` already appears
  today.

## Proposed architecture

Approved as designed in `design-02.md`, unchanged from `architecture-01.md`'s
approval of `design-01.md` — no components added, removed or altered by this
pass, beyond the guard test now being written into the design itself rather
than appended by architecture:

1. `PullRequestState` (`ports/forge.py`) + `Forge.pull_request_state(task)`,
   implemented by `GithubForge`, `FakeForge`, `MemoryForge`.
2. `GithubClient.get_pull_request(repo, *, number)` + `PullRequestDetail`,
   implemented by `HttpGithubClient`/`FakeGithubClient`.
3. `BehaviorResult.data: dict[str, Any] | None`, merged into `task.data` by
   `Consumer._deliver`; `LandingBehavior` populates `data={"pr": {...}}`.
4. `archived/` as a fourth `FilesystemTaskQueue`, parallel to `done`/`failed`.
5. `PrWatcher` (new core component, `pr_watcher.py`), plus its
   import-boundary guard test in the same commit.
6. `BoardProjection.archive()` + `_register()` + the `snapshot()` fix.
7. `ProjectionSink`'s `"archived"` branch; `SourceReflectorSink` untouched.
8. `--pr-poll` CLI flag, default `0` (disabled), following `--api-port 0`.

### Verified bugs the design's fixes actually close

Re-verified against current source this pass, not carried forward on trust:

**`BoardProjection.snapshot()` `KeyError` — confirmed real, fix is
mandatory.** `src/harness/projection.py:96-98` today does an unconditional
`self._columns[task_id] == name` inside the generator that builds each
column's task tuple. The design's `archive()` path (`_register`) deliberately
leaves a task in `_tasks` while popping it from `_columns`. Without the
`.get(task_id) == name` guard `design-02.md` §6a proposes, the very next
`snapshot()` call after any archive raises `KeyError` — for the whole board,
not just the archived task's listing. Must ship in the same commit as
`archive()`, not as a follow-up.

**`Harness.recover()` gap — confirmed real, fix is mandatory.**
`src/harness/app.py:121` recovers `[self._inbox, *self._step_queues.values()]`
only; `self._done` is absent. Today that's correct because nothing ever
claims out of `done/` — `dispatcher._finish` only *writes* into it, nothing
reads/claims it back out. `PrWatcher._archive` breaks that assumption: it
calls `self._done.claim(...)` before `self._done.transfer(...)`. A crash
between those two calls strands the task in `done/.processing/`, which
`FilesystemTaskQueue.list()` never sees (it globs `self._root`, not
`self._processing`) and therefore `BoardProjection.hydrate()` never sees
either — a silent, permanent disappearance from the board on restart, worse
than the bug being fixed. Fix: add `self._done` to `Harness.recover()`'s
queue list. Invariant #6 (`recover()` before `hydrate()`) is what makes this
safe — the call order in `Harness.run()` already puts `recover()` first.

Both fixes are no-ops for every existing install (no `PrWatcher` wired, or
`--pr-poll 0`): `done/.processing/` is always empty without a claimant, and
`_register()`/`archive()` is simply never called without an `"archived"`
event.

### The import-boundary guard test — now part of the design, not an add-on

`architecture-01.md` flagged this as missing from `design-01.md`;
`design-02.md` §8 has since folded it in as an explicit component
(`test_pr_watcher_imports_only_ports_and_models`, structurally identical to
`test_source_poller_imports_only_ports_and_models`). Nothing further to add
here — confirming this is the one substantive change between the two design
passes, and it's the correct one: without it, nothing stops a future edit
from importing `drivers.github_forge` directly into `pr_watcher.py` "just
this once."

## Implementation guidance

Follow `design-02.md`'s component design (§1-§8) and data schemas verbatim —
they're correct as written, unchanged from `design-01.md`'s signatures, and
this pass found nothing to alter. Sequencing, with the two mandatory fixes
folded into the right step:

1. `GithubClient.get_pull_request` + `PullRequestDetail` (+ `FakeGithubClient`
   test helper `close_pull_request`). Unit-test open/merged/closed mapping in
   isolation before touching `GithubForge`.
2. `PullRequestState` + `Forge.pull_request_state` on all three drivers
   (`GithubForge`, `FakeForge` incl. `prs.json` schema/`close_pull_request`
   helper, `MemoryForge` incl. `close` helper).
3. `BehaviorResult.data` + `Consumer._deliver` merge + `LandingBehavior`
   writing `{"pr": {...}}`. Re-run the full suite here — this is the one
   change that touches a shared, heavily-exercised type; it's cheap insurance
   before building the rest on top of it.
4. `archived/` queue: `HarnessLayout.archived`, `build()` constructs it and
   always constructs `PrWatcher` (cheap, side-effect-free until ticked — same
   posture as `landing` always being built).
5. `pr_watcher.py` itself, **plus** the import-boundary guard test
   (`test_pr_watcher_imports_only_ports_and_models`) in the same commit.
   Unit-test `tick()`: resolves→archives, still-open→untouched, forge
   error→isolated (mirror `SourcePoller.tick`'s existing test shape).
6. `BoardProjection.archive()`/`_register()`/**the `snapshot()` fix**
   together — do not split the fix from the feature that exposes it.
   `hydrate()` grows an optional `archived` parameter; `Harness.__init__`/
   `run()` thread it through, and this is also where **the `recover()` fix**
   belongs, in the same commit as wiring `PrWatcher` into `Harness` — the bug
   doesn't exist until `PrWatcher` claims from `done`, so fix and cause ship
   together.
7. `ProjectionSink`'s `"archived"` branch.
8. `cli.py`: `--pr-poll` (default `0.0`), threaded through `_run`/`serve`
   exactly like `--source-poll`.
9. Extend `tests/test_smoke_git.py`: real git + `FakeForge`, close a PR via
   the new helper, assert it drops off `snapshot()` and is still `get()`-able.
   Run the full suite (`.venv/bin/pytest -q`) — confirm
   `test_architecture.py` passes both unmodified *and* with the new guard
   test added.

### Data flow (unchanged from design-02.md §9)

`land` → `Consumer._deliver` merges `data["pr"]` → dispatcher moves task to
`done` (status=`end`) → `PrWatcher.tick()` (its own loop, own interval) finds
it in `done.list()`, queries `Forge.pull_request_state`, on resolution claims,
appends history, sets `status="archived"`, transfers `done → archived`, emits
`"archived"` → `ProjectionSink.archive(task)` drops it from every column while
keeping it in `BoardProjection._tasks` for `get()`.

## Resolved open questions

`plan-02.md` records these as decisions already, not open questions — this
pass re-confirms none of the reasoning needs revisiting:

- **Poll interval default: 300s.** Approved. Coarser than `--source-poll`'s
  30s is correct — a merge is not time-sensitive the way ingesting a new
  issue is, and GitHub's API budget is shared with the source poller in the
  same process.
- **Issue-side signaling on closed-unmerged: out of scope.** Approved. This
  task is about the harness's own board, not GitHub issue bookkeeping;
  conflating them would also require deciding *what* to do to the issue,
  which has no obvious right answer and belongs in its own task if wanted.
- **"Gone but still fetchable by id": approved as the shape**, not literal
  deletion and not a dedicated visible column. It costs nothing extra to
  implement (it's what `_register` already gives you for free) and it's the
  only option that doesn't foreclose a future "show archived tasks" toggle.
- **No backfill for tasks already in `done/`:** approved. They have no
  `data["pr"]`, `PrWatcher.tick()` skips them by construction (`isinstance(pr,
  dict)` guard), and they remain permanently in `done/` until manually dealt
  with. Acceptable residue, not a defect — a backfill would need to re-derive
  a PR reference from git history per task, real and separate work not
  justified by this task's brief.

## Risks and mitigations

- **A silent board outage if the `snapshot()` fix ships separately from
  `archive()`.** Mitigation: one commit, not two; a unit test that calls
  `archive()` then `snapshot()` in sequence, not just `archive()` then
  `get()`.
- **A silent task loss on crash if the `recover()` fix ships separately from
  `PrWatcher` wiring.** Same mitigation — one commit, plus a test that
  simulates a crash (in-memory: call `claim()` without the matching
  `transfer()`) between claim and transfer, then asserts `recover()` +
  `hydrate()` still surfaces the task in `done`.
- **A new core module with no import-boundary test is a standing invitation
  to leak a driver into it.** Mitigation: the guard test ships in the same
  commit as `pr_watcher.py` (step 5), not as a follow-up — `design-02.md` §8
  already treats it as a required component, closing the gap
  `architecture-01.md` flagged against `design-01.md`.
- **`prs.json` schema change breaks an existing fixture file with no
  `state`/`merged` keys.** Mitigation already in the design: default missing
  `state` to `"open"`, missing `merged` to `false`, on read — matches how the
  rest of `fake_forge.py`/`fs_queue.py` already treats "absent old field" as
  "default", not "error" (e.g. `Task.from_dict`'s `raw.get(...)` throughout
  `models.py`).
- **Forge outage degrades to "board stops shrinking," confirmed, not just
  claimed.** `PrWatcher.tick()`'s per-task try/except plus the loop's
  tick-or-sleep shape (identical to `_source_loop`) means a down GitHub API
  produces `pr_watch_error` events and nothing else — no exception propagates
  into `asyncio.gather` in `Harness.run()`.
- **Repeating this same plan → design → architecture pass a third time if
  `development` times out again.** Not a design risk, but worth naming: the
  plan/design/architecture are now stable across two independent passes with
  zero substantive change (this retry only promoted an already-approved fix
  into the design's component list). If `development` fails again, the cause
  is almost certainly unrelated to this spec (as the prior timeout was) —
  a third planning pass would be pure overhead; investigate the
  `development` step's execution environment first.

## Prerequisites before implementation begins

None outstanding — every dependency this task needs already exists in `main`
(`Forge`, `SourcePoller` as the template, `BoardProjection`, `Harness.run()`'s
loop structure, the `done`/`failed` two-terminal-queue pattern to extend to
three). Re-confirmed this pass: `git status` is clean, no other in-flight
work touches these files, and no source drift occurred between
`architecture-01.md` and now. Proceed directly to `development`.
