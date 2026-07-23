# Architecture (retry 3): archive a task off the board once its PR resolves

> This is the third architecture pass for this task (the `development` step
> timed out twice before writing any code, unrelated to the spec both times).
> This document supersedes `architecture-02.md` for downstream steps.
>
> I did not carry forward the "stops at `0e6f5d8`" claim on trust — I
> independently re-ran the check this pass and it does **not** hold:
>
> ```
> git log --oneline -1 -- src/harness/models.py src/harness/ports/forge.py \
>   src/harness/consumer.py src/harness/dispatcher.py src/harness/projection.py \
>   src/harness/drivers/projection_events.py src/harness/source_poller.py \
>   src/harness/app.py src/harness/cli.py src/harness/drivers/fs_queue.py \
>   src/harness/drivers/fake_forge.py src/harness/drivers/memory.py \
>   src/harness/drivers/github_client.py src/harness/drivers/github_forge.py \
>   src/harness/behaviors/landing.py src/harness/task_control.py \
>   src/harness/router.py tests/test_architecture.py
> # -> 0c8027b fix: dummy writes where the agent does, and forge reports GitHub's reason
> ```
>
> `0c8027b` genuinely postdates `0e6f5d8` and touches `github_forge.py`
> (adds a module-level `_explain(error)` helper and changes
> `open_pull_request`'s except-clause to use it) and `dummy_behavior.py`
> (writes into `.artifacts/<task>/` instead of the gitignored `.harness/`).
> So the citation in `plan-01/02/03.md`, `design-01/02/03.md` and
> `architecture-01/02.md` — "git log stops at `0e6f5d8`" — is simply wrong,
> repeated verbatim across three retries without anyone re-running it against
> the full file list.
>
> That said, **this is not drift introduced during this task.** Checking
> topology: `git merge-base --is-ancestor 0c8027b a598d25` (the commit that
> introduced `plan-01.md`) succeeds — `0c8027b` sits *before* `plan-01.md` in
> the graph, by about 15 hours (`2026-07-20 11:36` vs. `2026-07-21 02:05`).
> Every planning/design pass on this task, including the very first one, was
> already working against a tree that included `0c8027b`. The wrong hash was
> never a live risk; it's a copy-paste error in the verification preamble that
> happened to cite a stale-but-harmless boundary. I re-verified the substance
> directly against today's file contents rather than the hash claim, and it
> confirms `design-03.md`'s technical assumptions are correct on their own
> terms, independent of the citation bug:
>
> - `src/harness/drivers/github_forge.py` **already has** `_explain(error)`
>   (line 28) and `open_pull_request`'s except-clause already calls it (line
>   125). `design-03.md` §1 says `pull_request_state` should raise with "same
>   `_explain` wrapping as `open_pull_request`'s except-clause" — that's
>   accurate against what's actually in the file today, not a forward
>   reference to something that doesn't exist yet.
> - `src/harness/projection.py:96-97` still does the unconditional
>   `self._columns[task_id] == name` inside `snapshot()`'s generator — read
>   directly this pass, confirmed present verbatim.
> - `src/harness/app.py:121-122` still recovers only `[self._inbox,
>   *self._step_queues.values()]` — `self._done` still absent — read directly
>   this pass, confirmed present verbatim.
> - `tests/test_architecture.py:90-100`
>   (`test_source_poller_imports_only_ports_and_models`) is still the
>   per-file pattern to mirror; `test_consumer_has_no_branch_on_outcome_value`
>   (line 175) walks `ast.Compare` nodes only — confirmed an unconditional
>   `if result.data:`/ternary merge (`ast.IfExp`, not `ast.Compare`) cannot
>   trip it.
> - `src/harness/models.py`'s `BehaviorResult` (line 26) still has only
>   `outcome`/`summary` — the `data` field design-03 proposes is additive, not
>   yet present, exactly as claimed.
> - `src/harness/behaviors/landing.py`, `drivers/fake_forge.py`,
>   `drivers/memory.py`'s `MemoryForge`, `src/harness/ports/forge.py` all
>   match the shapes `design-03.md` describes as "today's" for the
>   `open_pull_request`-only state.
>
> Conclusion: nothing has drifted in any way that affects this design. The
> only defect found this pass is documentary (a wrong commit hash repeated
> three times), not a design or source problem — noted here so a fourth retry
> doesn't have to rediscover it, but it changes nothing below.

## Alignment with existing patterns and integration points

Unchanged from `architecture-02.md`'s assessment, re-confirmed directly
against source rather than by trusting the prior write-up:

- `PrWatcher` modeled on `SourcePoller` is correct: both are periodic scans of
  already-committed state against an outside system (`TaskSource`/`Forge`),
  both must never block orchestration on a slow/down remote, and both have a
  documented failure-isolation shape to copy (`SourcePoller.tick`'s
  try/except around `poll()`). `PrWatcher` must isolate **per task** rather
  than per tick, because `pull_request_state` is one call per task, not one
  call for the whole batch — `design-03.md` gets this right (`pr_watch_error`
  emitted inside the loop, per task).
- `pr_watcher.py` sits at the same tier as `source_poller.py`: core, touching
  only `ports/{queue,forge,events,clock}` and `models`, confirmed by
  `imported_modules(SOURCE / "source_poller.py")`'s existing guard shape in
  `tests/test_architecture.py:90-100` — `design-03.md` §8 folds the parallel
  guard test for `pr_watcher.py` into the design itself, not left for
  architecture to flag as a gap (as `architecture-01.md` originally had to).
- `Forge.pull_request_state` extends `ports/forge.py` next to
  `open_pull_request` — same ABC (confirmed: `Forge(ABC)`, one
  `@abstractmethod` today), same "task in, port-level exception out"
  contract.
- `BehaviorResult.data` extends `models.py`, which imports nothing from the
  package (confirmed by reading the file) — a plain `dict[str, Any] | None`
  needs no new import.
- `Consumer._deliver`'s merge (`if result.data else task.data`) is not a
  branch on `result.outcome`'s value — confirmed this pass directly against
  `test_consumer_has_no_branch_on_outcome_value`'s current `ast.Compare`-only
  walk (line 175-194): an `if result.data:` guard, or the ternary the design
  writes, produces no `ast.Compare` node deriving from `outcome`, so the test
  cannot trip on it.
- `BoardProjection.archive()`/`ProjectionSink`'s new branch stay inside
  `projection.py`/`drivers/projection_events.py`; `api/` only reaches the
  board through `BoardView` — no new surface for `api/` to touch (invariant
  #5 holds), confirmed by `app.py`'s wiring (`BoardProjection` constructed
  once in `build()`, handed to `Harness`, never touched by anything outside
  the events pipeline and `api/`).
- `dispatcher.py`/`consumer.py` gain no new import — `PrWatcher` is wired in
  `app.py` alongside `SourcePoller`, exactly where `Forge` already appears
  (`build()`'s `forge = forge or MemoryForge()`, confirmed at line 231).

## Proposed architecture

Approved as designed in `design-03.md`, unchanged in substance from
`design-01.md`/`design-02.md` — no components added, removed or altered by
this pass:

1. `PullRequestState` (`ports/forge.py`) + `Forge.pull_request_state(task)`,
   implemented by `GithubForge`, `FakeForge`, `MemoryForge`.
2. `GithubClient.get_pull_request(repo, *, number)` + `PullRequestDetail`,
   implemented by `HttpGithubClient`/`FakeGithubClient`.
3. `BehaviorResult.data: dict[str, Any] | None`, merged into `task.data` by
   `Consumer._deliver`; `LandingBehavior` populates `data={"pr": {...}}`.
4. `archived/` as a fourth `FilesystemTaskQueue`, parallel to `done`/`failed`
   (`HarnessLayout` today has exactly `workflows`/`tasks`/`queues`/`done`/
   `failed`/`worktrees`/`agents`/`repos` — confirmed by reading `app.py`;
   `archived` is a clean, additive property).
5. `PrWatcher` (new core component, `pr_watcher.py`), plus its
   import-boundary guard test in the same commit.
6. `BoardProjection.archive()` + `_register()` + the `snapshot()` fix.
7. `ProjectionSink`'s `"archived"` branch; `SourceReflectorSink` untouched.
8. `--pr-poll` CLI flag, default `0` (disabled), following the `--api-port 0`
   convention.

### Verified bugs the design's fixes actually close

Re-read directly against current source this pass (not carried forward on
trust from `architecture-02.md`):

**`BoardProjection.snapshot()` `KeyError` — confirmed real, fix is
mandatory.** `src/harness/projection.py:96-97` today does an unconditional
`self._columns[task_id] == name` inside the generator that builds each
column's task tuple (confirmed by direct read this pass). The design's
`archive()` path (`_register`) deliberately leaves a task in `_tasks` while
popping it from `_columns`. Without the `.get(task_id) == name` guard
`design-03.md` §6a proposes, the very next `snapshot()` call after any
archive raises `KeyError` for the whole board, not just the archived task's
listing. Must ship in the same commit as `archive()`, not as a follow-up.

**`Harness.recover()` gap — confirmed real, fix is mandatory.**
`src/harness/app.py:121-122` recovers `[self._inbox,
*self._step_queues.values()]` only; `self._done` is absent (confirmed by
direct read this pass). Today that's correct because nothing claims out of
`done/` — `dispatcher._finish` (confirmed at `dispatcher.py:99`) only writes
into `done`/`failed`, nothing reads/claims either back out.
`PrWatcher._archive` breaks that assumption: it calls `self._done.claim(...)`
before `self._done.transfer(...)`. A crash between those two calls strands
the task in `done/.processing/`, which `FilesystemTaskQueue.list()` never
sees and therefore `BoardProjection.hydrate()` never sees either — a silent,
permanent disappearance from the board on restart, worse than the bug being
fixed. Fix: add `self._done` to `Harness.recover()`'s queue list. Invariant #6
(`recover()` before `hydrate()`) is what makes this safe — confirmed the call
order in `Harness.run()` (`app.py:158-164`) already puts `recover()` first.

Both fixes are no-ops for every existing install (no `PrWatcher` wired, or
`--pr-poll 0`): `done/.processing/` stays empty without a claimant, and
`_register()`/`archive()` is simply never called without an `"archived"`
event.

### The import-boundary guard test — part of the design, confirmed correct shape

`design-03.md` §8 carries forward `test_pr_watcher_imports_only_ports_and_models`,
structurally identical to `test_source_poller_imports_only_ports_and_models`
(re-read directly this pass at `tests/test_architecture.py:90-100` — the
pattern is unchanged and still the right one to copy: walk
`imported_modules(SOURCE / "pr_watcher.py")`, assert every `harness.*` import
is `harness.models` or starts with `harness.ports`).

## Implementation guidance

Follow `design-03.md`'s component design (§1-§8) and data schemas verbatim —
re-verified this pass, correct as written, no drift from `design-01.md`'s
original signatures. Sequencing, with the two mandatory fixes folded into the
right step:

1. `GithubClient.get_pull_request` + `PullRequestDetail` (+
   `FakeGithubClient` test helper `close_pull_request`). Unit-test the
   open/merged/closed mapping in isolation before touching `GithubForge`.
2. `PullRequestState` + `Forge.pull_request_state` on all three drivers
   (`GithubForge` — reuse `_repo_path`/`_slug_of` and the existing `_explain`
   helper for its error wrapping, exactly as `design-03.md` §1 specifies;
   `FakeForge` incl. `prs.json` schema/`close_pull_request` helper;
   `MemoryForge` incl. `close` helper).
3. `BehaviorResult.data` + `Consumer._deliver` merge + `LandingBehavior`
   writing `{"pr": {...}}`. Re-run the full suite here — this is the one
   change touching a shared, heavily-exercised type; cheap insurance before
   building the rest on top of it.
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
   Run the full suite (`.venv/bin/pytest -q`) — confirm `test_architecture.py`
   passes both unmodified *and* with the new guard test added.

### Data flow (unchanged from design-03.md §9)

`land` → `Consumer._deliver` merges `data["pr"]` → dispatcher moves task to
`done` (status=`end`) → `PrWatcher.tick()` (its own loop, own interval) finds
it in `done.list()`, queries `Forge.pull_request_state`, on resolution claims,
appends history, sets `status="archived"`, transfers `done → archived`, emits
`"archived"` → `ProjectionSink.archive(task)` drops it from every column while
keeping it in `BoardProjection._tasks` for `get()`.

## Resolved open questions

Carried forward as decisions, not reopened — three independent passes now
agree and this one found no reason to revisit:

- **Poll interval default: 300s.** Coarser than `--source-poll`'s 30s is
  correct — a merge is not time-sensitive the way ingesting a new issue is,
  and GitHub's API budget is shared with the source poller in the same
  process.
- **Issue-side signaling on closed-unmerged: out of scope.** This task is
  about the harness's own board, not GitHub issue bookkeeping; conflating
  them would also require deciding *what* to do to the issue, which has no
  obvious right answer and belongs in its own task if wanted.
- **"Gone but still fetchable by id": approved as the shape**, not literal
  deletion and not a dedicated visible column. Costs nothing extra to
  implement (it's what `_register` gives for free) and doesn't foreclose a
  future "show archived tasks" toggle.
- **No backfill for tasks already in `done/`:** approved. They have no
  `data["pr"]`, `PrWatcher.tick()` skips them by construction, and they
  remain permanently in `done/` until manually dealt with. Acceptable
  residue, not a defect.

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
  commit as `pr_watcher.py` (step 5).
- **`prs.json` schema change breaks an existing fixture file with no
  `state`/`merged` keys.** Mitigation already in the design: default missing
  `state` to `"open"`, missing `merged` to `false`, on read — matches how the
  rest of `fake_forge.py`/`fs_queue.py` already treats "absent old field" as
  "default", not "error".
- **Forge outage degrades to "board stops shrinking," confirmed, not just
  claimed.** `PrWatcher.tick()`'s per-task try/except plus the loop's
  tick-or-sleep shape (identical to `_source_loop`) means a down GitHub API
  produces `pr_watch_error` events and nothing else — no exception propagates
  into `asyncio.gather` in `Harness.run()`.
- **A stale citation ("stops at `0e6f5d8`") repeated across three retries
  without anyone re-running the check against the full file list.** Not a
  design risk — it never pointed at a real gap, since the actual last-touching
  commit (`0c8027b`) predates even `plan-01.md`. Noted purely so a future pass
  doesn't need to re-discover the discrepancy; it requires no corrective
  action in the design or code.
- **A third repeated planning cycle if `development` times out again.** Not a
  design risk. Plan/design/architecture are stable across three independent
  passes with zero substantive change since `design-01.md`/`architecture-01.md`.
  If `development` fails a third time, treat it as environmental — investigate
  the `development` step's execution environment, not the spec.

## Prerequisites before implementation begins

None outstanding. Re-confirmed this pass by direct inspection (not by
trusting prior artifacts' claims): `Forge`, `SourcePoller` (the template),
`BoardProjection`, `Harness.run()`'s loop structure, and the `done`/`failed`
two-terminal-queue pattern (to extend to three) all exist in the tree exactly
as `design-03.md` assumes. `git status` is clean. Proceed directly to
`development`.
