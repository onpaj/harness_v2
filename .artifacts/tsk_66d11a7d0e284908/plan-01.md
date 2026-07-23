# Poll for merged PRs and remove their completed tasks from the dashboard

## Summary

Once `LandingBehavior` opens a pull request, the harness loses interest in the
task — it sits in the `done` column forever, whether the PR is later merged,
rejected, or simply forgotten. This task closes that loop: a new reconciler
periodically checks GitHub for the merge state of each `done` task's PR and,
once merged, retires the task from the live dashboard — symmetric to how
`SourcePoller` pulls work *in*, this pulls the outcome back *in* and archives
what's actually finished.

## Context

`done` today means "landing opened a PR", not "the change shipped". The board
(`docs/superpowers/specs/2026-07-19-board-ui-design.md`'s dashboard) has no
notion of a task's PR ever being merged — `BoardProjection` only ever adds
tasks to columns (`apply`/`_store`), never removes them, and nothing on the
`Task` records which PR it produced in a structured, queryable way (only a
free-text summary line, `"opened PR {url}"`, in the history). As the harness
runs longer, `done` accumulates every task that ever landed, merged or not,
which defeats its purpose as "what's actually outstanding to look at". This
task makes `done` self-cleaning for the common case (PR merged → nothing left
to review) while keeping the audit trail intact.

## Functional requirements

**FR-1 — Landing records structured PR identity on the task.**
When `LandingBehavior` opens (or finds an existing) PR, the task gains
`task.data["pr"] = {"repo": "<owner>/<repo>", "number": <int>, "url": "<url>",
"branch": "<branch>"}`, alongside the existing free-text summary.
- AC: after a task lands via `GithubForge`, `GET /api/tasks/{id}` returns
  `data.pr.repo`/`number`/`url`/`branch` populated.
- AC: a task landed through `FakeForge`/`MemoryForge` (no real GitHub) still
  gets a `data.pr` block from the `PullRequest` the fake returns; the
  reconciler (FR-3) simply never reports it merged, since there is nothing
  behind `repo` to poll.

**FR-2 — A `MergeChecker` capability reports whether a task's PR is merged.**
A new read-only capability, `is_merged(task) -> bool | None`, where `None`
means "nothing to check" (no `data.pr` on the task). A GitHub-backed driver
calls the API for `{repo}/pulls/{number}` and reads the `merged` field; a fake
driver exists for tests.
- AC: given a task with `data.pr` pointing at a merged PR, `is_merged` returns
  `True`; at an open PR, `False`; for a task without `data.pr`, `None`.
- AC: a transient failure (network/API error) raises rather than silently
  returning `False`, so the reconciler can tell "not merged" from "couldn't
  check" and simply retry later instead of never archiving a merged task.

**FR-3 — A reconciliation loop periodically archives merged tasks.**
A new core loop (ports/models only, same shape as `SourcePoller`) runs
alongside the dispatcher/consumer/source loops in `Harness.run()`, on its own
interval (a remote API call, so it must not run at the tight local
`poll_interval`). Each tick: look at one `done` task carrying `data.pr`, ask
the `MergeChecker`; if merged, claim it out of `done` and transfer it into a
new terminal queue, `archived/` — the same `TaskQueue` port, no new queue
capability needed. A history entry records the transition (auditable, like
every other move).
- AC: a `done` task with `data.pr` and a `MergeChecker` fake reporting merged
  → one tick moves it from `done` to `archived` and fires an `archived` event.
- AC: a `done` task without `data.pr` is left alone — never inspected, never
  errors.
- AC: a `done` task whose PR is open is left in `done` and re-checked later.
- AC: a `MergeChecker` exception does not stop the loop — the tick returns
  `False`, the loop sleeps and retries, exactly like `SourcePoller.tick`.
- AC: killing the process between `claim()` and `transfer()` and restarting →
  `recover()` puts the task straight back into `done` (no new recovery code:
  `archived/` recovers like any other queue).

**FR-4 — Archived tasks disappear from the live board but stay auditable.**
`BoardProjection` gains a way to drop a task out of its rendered columns
(`snapshot()` stops listing it under `done`) while it remains fetchable by id
(`get(task_id)`) with its full history, `data.pr` and artifacts — we declutter
the live view, we don't destroy the record.
- AC: `GET /api/board` no longer lists an archived task in any column.
- AC: `GET /api/tasks/{id}` for an archived task still returns 200, including
  the archive history entry and `data.pr`.
- AC: after a restart, hydration does not re-surface an archived task into a
  rendered column (the `archived/` queue is simply not part of
  `column_order`, same as any queue that isn't `done`/`failed`/a step).

**FR-5 — No new operator-facing controls.**
No "Archived" column, no manual "archive now" action, no configurable
interval in the UI. Tasks vanish from the board once their PR merges — that's
the entire ask (see Dependencies and scope).

## Non-functional requirements

- **Rate limits.** At most one GitHub call per candidate task per tick, on a
  conservative interval (same spirit as `SourcePoller`'s `source_interval`,
  default in the tens of seconds, not the local loop's `poll_interval`).
- **Crash safety.** No bespoke recovery logic: `archived/` is a `TaskQueue`
  like `done/failed`, so the existing `claim`/`recover` machinery already
  makes a half-finished archive resumable.
- **Idempotence.** Re-checking an already-archived task, or a tick that finds
  nothing to do, has no side effect — matches the "outward projection is
  idempotent" ethos already established for `TaskSource`.
- **No new production dependency.** The real merge check runs over stdlib
  `urllib` via `GithubClient`, exactly like the rest of the GitHub surface.

## Data model

- `Task.data["pr"]` (new, optional): `{"repo": str, "number": int, "url": str,
  "branch": str}` — the task's *outbound* landing identity, written once by
  `LandingBehavior`. Symmetric to the existing `Task.data["source"]`
  convention for a task's *inbound* origin.
- `BehaviorResult` gains an optional `data: dict[str, Any] | None = None`
  field; the consumer merges it into `task.data` on delivery. This is the
  minimal extension needed to let a behavior attach structured facts to a
  task — it does not touch `outcome`, so invariant 2 (no branch on outcome in
  the consumer) is unaffected.
- `PullRequest` (`ports/forge.py`) gains a `repo: str` field (the GitHub
  slug), populated by `GithubForge` (it already computes `slug` internally)
  so `LandingBehavior` doesn't have to re-derive it.
- New terminal queue `archived/`, same shape as `done/`/`failed/` — plain
  `Task` JSON, nothing new in the model. `HarnessLayout.archived` path added.

## Interfaces

- **`ports/merge.py` (new):**
  ```python
  class MergeChecker(ABC):
      def is_merged(self, task: Task) -> bool | None: ...
  ```
  Kept separate from `Forge` (which only *opens* PRs) — mirrors the existing
  read/write split between `BoardView` and `TaskControl`.
- **`drivers/github_merge_checker.py` (new):** `GithubMergeChecker(client:
  GithubClient)`, calling a new `GithubClient.get_pull_request(repo, number)`
  (extends `FakeGithubClient` and `HttpGithubClient`) that reports at least
  `merged: bool`.
- **`src/harness/merge_reconciler.py` (new, core):**
  `MergeReconciler(*, done: TaskQueue, archived: TaskQueue, checker:
  MergeChecker, events: EventSink, clock: Clock)`, `tick() -> bool` — same
  call shape as `SourcePoller`/`Consumer`. Imports only ports and models
  (enforced the way `test_source_poller_imports_only_ports_and_models`
  enforces it for `SourcePoller`).
- **`Harness.run()`:** one more gathered loop (`_reconcile_loop`), its own
  `reconcile_interval` parameter, following the `_source_loop` pattern
  exactly.
- **`app.py build(...)`:** gains `merge_checker: MergeChecker | None = None`
  (omitted → no reconciliation, current behavior unchanged) and wires the
  `archived` queue + `MergeReconciler` when supplied.
- **`cli.py run`:** when `--github-repo`/`GITHUB_TOKEN` are present (the same
  condition that already wires `GithubForge`/`GithubTaskSource`), also wire
  `GithubMergeChecker`.
- **`BoardProjection`:** new method (e.g. `archive(task_id: str) -> None`)
  that drops the task from `_columns` (so `snapshot()` stops listing it)
  while leaving it in `_tasks` (so `get()` keeps working). `ProjectionSink`
  routes the `"archived"` event to it instead of `apply()`.
- No new HTTP endpoint required — the existing `/api/board` and
  `/api/tasks/{id}` already express the right thing once the projection
  change lands.

## Dependencies and scope

Builds on the already-shipped phase 3 (`GithubForge`, `RepositoryRegistry`)
and phase 4 (`GithubClient`, the poller/reflector pattern) work. No change to
`router.py`, workflow routing, or the consumer's outcome semantics.

**In scope:** structured PR identity on the task; a new read port + GitHub
driver for merge status; a new core reconciliation loop; a new terminal
`archived/` queue; board projection hiding archived tasks; wiring in
`app.py`/`cli.py`; architecture-test coverage extending `test_architecture.py`
(new core module imports only ports/models; `dispatcher.py`/`consumer.py`
never import `ports.merge`).

**Out of scope:**
- Any "Archived" view/column/filter in the UI — tasks simply leave the board.
- Changing what happens to the *originating GitHub issue* on merge (that's
  `GithubTaskSource.finish`, already fired at `end`, unrelated to PR state).
- Retry/backoff policy beyond "skip on error, try again next tick".
- Backfilling `data.pr` for tasks that landed before this feature existed —
  they simply have no `data.pr` and are never selected for archival.
- Any change to `FakeForge`/`MemoryForge` runtime behavior beyond adding the
  `repo` field to the `PullRequest` they already return.

## Rough plan

1. `BehaviorResult.data` + consumer merges it into `task.data` on delivery
   (`tests/test_consumer.py`).
2. `PullRequest.repo` field; `GithubForge`/`FakeForge`/`MemoryForge` populate
   it; `LandingBehavior` builds `task.data["pr"]` from the returned
   `PullRequest` (`tests/test_landing.py`, `tests/test_github_forge.py`).
3. `ports/merge.py` (`MergeChecker`) + `GithubClient.get_pull_request`
   (`FakeGithubClient`/`HttpGithubClient`) + `GithubMergeChecker` driver + an
   in-memory fake for tests.
4. `merge_reconciler.py` (core `MergeReconciler`) + unit tests mirroring
   `tests/test_source_poller.py`.
5. `HarnessLayout.archived` + wiring in `app.py` (`archived` queue,
   `MergeReconciler`, new `build(...)` parameter) + `Harness.run()` /
   `_reconcile_loop`.
6. `BoardProjection.archive()` + `ProjectionSink` routing for the
   `"archived"` event; board/API tests confirming an archived task is gone
   from `/api/board` but `GET /api/tasks/{id}` still resolves it.
7. `cli.py` wiring (`GithubMergeChecker` alongside the existing
   `--github-repo`/`GITHUB_TOKEN` condition) + extend `test_architecture.py` +
   an in-memory e2e test analogous to `tests/test_phase4_e2e.py` (land a
   task, mark its PR merged in the fake, tick the reconciler, assert it left
   `done` and the board).
8. Update `CLAUDE.md` (module map, next invariant numbers) once the
   architecture step has settled the exact port/queue shape.

## Open questions

- **Merge-check boundary:** proposed default is a dedicated `MergeChecker`
  port, not an extension of `Forge` — confirm in the architecture step.
- **`archived/` as a real queue vs. a flag on `done`:** proposed default is a
  real queue (free crash-recovery via existing `claim`/`recover`, consistent
  with every other terminal state) over adding an `archivedAt` field that the
  projection would have to filter on.
- **Retention:** proposed default is to keep `archived/` on disk indefinitely,
  same as `done/failed` today — no expiry/GC in this task.
- **Reconcile interval and batching:** proposed default is one candidate task
  per tick, interval analogous to `source_interval` (tens of seconds) —
  confirm the exact number with the architecture step.
- **Should the originating GitHub issue's label change on merge** (e.g. a
  `harness:merged` label distinct from `harness:pr-open`)? Not asked for by
  this task (which is about the dashboard, not the issue) — flagged here for
  a possible follow-up, left out of scope for now.
