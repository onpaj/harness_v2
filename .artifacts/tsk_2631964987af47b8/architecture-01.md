# Architecture: archive a task off the board once its PR resolves

I read `plan-01.md` and `design-01.md` in full and cross-checked every
component they propose against the current source (`models.py`, `ports/`,
`consumer.py`, `dispatcher.py`, `projection.py`, `drivers/projection_events.py`,
`source_poller.py`, `app.py`, `cli.py`, `drivers/fs_queue.py`,
`drivers/fake_forge.py`, `drivers/memory.py`, `drivers/github_client.py`,
`drivers/github_forge.py`, `behaviors/landing.py`, `task_control.py`,
`router.py`, `tests/test_architecture.py`). The design holds up: every
signature it proposes matches the real shape of its neighbors, and both of the
"[resolved]" fixes it calls out against `projection.py`/`app.py` are real bugs
in the current code, not hypothetical ones — I verified them directly (below).
This assessment approves the plan/design as the spec, resolves the open
questions definitively, promotes two of the design's "resolved" items from
optional to mandatory, and adds one guard the design didn't mention.

## Alignment with existing patterns and integration points

The design's central move — model `PrWatcher` on `SourcePoller` — is the
correct call, and for a specific reason beyond "it looks similar": both are
periodic scans of already-committed state against an outside system
(`TaskSource`/`Forge`), both must never block orchestration on a slow or down
remote, and both already have a documented failure-isolation shape to copy
(`SourcePoller.tick`'s try/except around `poll()`, `CompositeEventSink`
isolating sink failures). Reusing that shape is not just consistent, it's the
only shape here that satisfies FR-3's AC ("a forge failure... does not stop
other tasks in that tick from being checked") — SourcePoller isolates at the
level of one `poll()` call because GitHub's API is one call; PrWatcher must
isolate per-task because `pull_request_state` is one call *per task*. The
design gets this right (`pr_watch_error` is per-task, not per-tick).

Every one of the design's touch points is exactly where the module map says
core/ports work belongs, not where a driver's business would leak in:

- `PrWatcher` in a new `pr_watcher.py`, sibling to `source_poller.py`, touching
  only `ports/{queue,forge,events,clock}` and `models`. Confirmed
  `source_poller.py` today imports nothing else
  (`test_source_poller_imports_only_ports_and_models` already enforces this
  shape for its sibling) — `pr_watcher.py` must satisfy the same constraint.
- `Forge.pull_request_state` extends `ports/forge.py` next to
  `open_pull_request` — same ABC, same "task in, port-level exception out"
  contract (`ports/forge.py:23-30`).
- `BehaviorResult.data` extends `models.py`, which "imports nothing from the
  package" (`models.py:1`) — confirmed the new field is a plain
  `dict[str, Any] | None`, no new import required.
- `Consumer._deliver`'s merge is an unconditional `if result.data else
  task.data`, not a branch on `result.outcome` — I checked this against
  `test_consumer_has_no_branch_on_outcome_value`
  (`tests/test_architecture.py:175-194`), which parses the whole module via
  `ast` for *any* comparison deriving from `outcome`. An `if result.data:` is
  not such a comparison (it doesn't touch `outcome` at all), so the guard
  stays green. Confirmed safe.
- `BoardProjection.archive()`/`ProjectionSink`'s new branch stay inside
  `projection.py`/`drivers/projection_events.py`, which `api/` only reaches
  through `BoardView` — no new surface for `api/` to touch, matching invariant
  #5.
- `dispatcher.py`/`consumer.py` gain no new import. `PrWatcher` is wired in
  `app.py` alongside `SourcePoller`, exactly where `Forge` already appears
  today (`app.py:34, 209, 231, 275-281`).

One thing the design didn't flag but should: `tests/test_architecture.py`
guards `source_poller.py`'s import boundary *by name*
(`test_source_poller_imports_only_ports_and_models`, line 90). There is no
generic "every core module obeys this" test — the guard is per-file. A new
core module (`pr_watcher.py`) gets **no automatic protection** unless a
parallel test is written for it. This is a prerequisite, not a nice-to-have
(see Implementation guidance).

## Proposed architecture

Approved as designed, with two "[resolved]" items promoted from optional to
required (they fix real bugs, not style) and the open questions closed:

### Components (unchanged from design-01.md)

1. `PullRequestState` (`ports/forge.py`) + `Forge.pull_request_state(task)`,
   implemented by `GithubForge`, `FakeForge`, `MemoryForge`.
2. `GithubClient.get_pull_request(repo, *, number)` + `PullRequestDetail`,
   implemented by `HttpGithubClient`/`FakeGithubClient`.
3. `BehaviorResult.data: dict[str, Any] | None`, merged into `task.data` by
   `Consumer._deliver`; `LandingBehavior` populates `data={"pr": {...}}`.
4. `archived/` as a fourth `FilesystemTaskQueue`, parallel to `done`/`failed`.
5. `PrWatcher` (new core component, `pr_watcher.py`).
6. `BoardProjection.archive()` + `_register()` + the `snapshot()` fix.
7. `ProjectionSink`'s `"archived"` branch; `SourceReflectorSink` untouched.
8. `--pr-poll` CLI flag, default `0` (disabled), following `--api-port 0`.

### Verified bugs the design's fixes actually close

I ran these against the current code, not just the design's description:

**`BoardProjection.snapshot()` KeyError — confirmed real, fix is mandatory.**
`projection.py:96-98` today does
`(task for task_id, task in self._tasks.items() if self._columns[task_id] ==
name)` — an unconditional `self._columns[task_id]`. The design's `archive()`
path (`_register`) deliberately leaves a task in `_tasks` while popping it
from `_columns`. Without the `.get(task_id) == name` guard the design
proposes, the very next `snapshot()` call after an archive raises
`KeyError`, not "silently omits the task." This turns the feature into an
outage of the whole board, not a cosmetic listing bug — it must ship in the
same PR as `archive()`, not as a follow-up.

**`Harness.recover()` gap — confirmed real, fix is mandatory.**
`app.py:121-126` recovers `[inbox, *step_queues.values()]` only. Today that's
correct because nothing ever claims out of `done/` — `dispatcher._finish`
only *writes* into it (`dispatcher.py:99-111`), nothing reads/claims it back
out. `PrWatcher._archive` breaks that assumption: it calls
`self._done.claim(...)` before `self._done.transfer(...)`. A crash between
those two calls stripped a task into `done/.processing/`, which
`done.list()` never sees (`FilesystemTaskQueue.list()` globs `self._root`,
not `self._processing` — `drivers/fs_queue.py:59-65`) and therefore
`BoardProjection.hydrate()` never sees either. That's a silent, permanent
disappearance from the board on restart, worse than the bug being fixed. Add
`self._done` to `Harness.recover()`'s queue list. Invariant #6 (`recover()`
before `hydrate()`) is exactly what makes this safe — verified the call order
in `Harness.run()` (`app.py:158-164`) already puts `recover()` first.

Both fixes are no-ops for every existing install (no `PrWatcher` wired, or
`--pr-poll 0`): `done/.processing/` is always empty without a claimant, and
`_register()`/`archive()` is simply never called without an `"archived"`
event. Confirmed by inspection, not just asserted.

### One thing to add beyond the design: an import-boundary guard test

Add `test_pr_watcher_imports_only_ports_and_models` to
`tests/test_architecture.py`, structurally identical to the existing
`test_source_poller_imports_only_ports_and_models` (lines 90-100), pointed at
`pr_watcher.py`. Rationale above (per-file guards, no generic one). This is
cheap (it's a five-line copy with the filename changed) and it's the single
test that actually enforces invariant #11/#17-style boundaries for the new
module — without it, nothing stops a future edit from importing
`drivers.github_forge` directly into `pr_watcher.py` "just this once."

## Implementation guidance

Follow `design-01.md`'s component design (§1-§8) and data schemas verbatim —
they're correct as written and I found nothing to change in the signatures.
Sequencing, with the two mandatory fixes folded into the right step:

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
5. `pr_watcher.py` itself, **plus** the new architecture-guard test from the
   previous section in the same commit. Unit-test `tick()`: resolves→archives,
   still-open→untouched, forge error→isolated (mirror
   `SourcePoller.tick`'s existing test shape).
6. `BoardProjection.archive()`/`_register()`/**the `snapshot()` fix** together
   — do not split the fix from the feature that exposes it. `hydrate()` grows
   an optional `archived` parameter; `Harness.__init__`/`run()` thread it
   through, and this is also where **the `recover()` fix** belongs, in the
   same commit as wiring `PrWatcher` into `Harness` — the bug doesn't exist
   until `PrWatcher` claims from `done`, so fix and cause ship together.
7. `ProjectionSink`'s `"archived"` branch.
8. `cli.py`: `--pr-poll` (default `0.0`), threaded through `_run`/`serve`
   exactly like `--source-poll`.
9. Extend `tests/test_smoke_git.py`: real git + `FakeForge`, close a PR via
   the new helper, assert it drops off `snapshot()` and is still `get()`-able.
   Run the full suite (`.venv/bin/pytest -q`) — confirm
   `test_architecture.py` passes both unmodified *and* with the new guard
   test added.

### Data flow (unchanged from design-01.md §8)

`land` → `Consumer._deliver` merges `data["pr"]` → dispatcher moves task to
`done` (status=`end`) → `PrWatcher.tick()` (its own loop, own interval) finds
it in `done.list()`, queries `Forge.pull_request_state`, on resolution claims,
appends history, sets `status="archived"`, transfers `done → archived`, emits
`"archived"` → `ProjectionSink.archive(task)` drops it from every column while
keeping it in `BoardProjection._tasks` for `get()`.

## Resolved open questions

The plan flagged four; deciding all four rather than leaving them for
development to guess at:

- **Poll interval default: 300s.** Approved as proposed. Coarser than
  `--source-poll`'s 30s is correct — a merge is not time-sensitive the way
  ingesting a new issue is, and GitHub's API budget is shared with the source
  poller in the same process.
- **Issue-side signaling on closed-unmerged: out of scope**, approved as
  proposed. This task is about the harness's own board, not GitHub issue
  bookkeeping; conflating them would also require deciding *what* to do to the
  issue, which has no obvious right answer (reopen? relabel? comment?) and
  belongs in its own task if wanted.
- **"Gone but still fetchable by id": approved as the shape**, not literal
  deletion and not a dedicated visible column. It costs nothing extra to
  implement (it's what `_register` already gives you for free) and it's the
  only option that doesn't foreclose a future "show archived tasks" toggle.
- **No backfill for tasks already in `done/`:** approved. They have no
  `data["pr"]`, `PrWatcher.tick()` skips them by construction (`isinstance(pr,
  dict)` guard), and they remain permanently in `done/` until manually dealt
  with. This is acceptable residue, not a defect — a backfill would need to
  re-derive a PR reference from git history per task, which is real,
  separate, and not justified by this task's brief.

## Risks and mitigations

- **A silent board outage if the `snapshot()` fix ships separately from
  `archive()`.** Mitigation: called out above as one commit, not two; add a
  unit test that calls `archive()` then `snapshot()` in sequence, not just
  `archive()` then `get()`.
- **A silent task loss on crash if the `recover()` fix ships separately from
  `PrWatcher` wiring.** Same mitigation — one commit, plus a test that kills
  the process (in-memory: simulate by calling `claim()` without the matching
  `transfer()`) between claim and transfer, then asserts `recover()` +
  `hydrate()` still surfaces the task in `done`.
- **A new core module with no import-boundary test is a standing invitation
  to leak a driver into it.** Mitigation: the guard test is a required part of
  step 5, not a follow-up.
- **`prs.json` schema change breaks an existing fixture file with no
  `state`/`merged` keys.** Mitigation already in the design: default missing
  `state` to `"open"`, missing `merged` to `false`, on read — verified this
  matches how the rest of `fake_forge.py`/`fs_queue.py` already treats
  "absent old field" as "default", not "error" (e.g. `Task.from_dict`'s
  `raw.get(...)` throughout `models.py:117-132`).
- **Forge outage degrades to "board stops shrinking," confirmed, not just
  claimed.** `PrWatcher.tick()`'s per-task try/except plus the loop's
  tick-or-sleep shape (identical to `_source_loop`, `app.py:190-197`) means a
  down GitHub API produces `pr_watch_error` events and nothing else — no
  exception propagates into `asyncio.gather` in `Harness.run()`.

## Prerequisites before implementation begins

None outstanding — every dependency this task needs already exists in `main`
(`Forge`, `SourcePoller` as the template, `BoardProjection`, `Harness.run()`'s
loop structure, the `done`/`failed` two-terminal-queue pattern to extend to
three). No other in-flight task in this repo's history touches the same files
in a conflicting way. Proceed directly to `development`.
