# Terminal-task retention — design

*2026-07-28*

## The request

> Clearing the dashboard — in the "No workflow" tab there are stale tasks. Can
> we somehow archive / remove them?

## What is actually there

The `No workflow` tab (internally `unknown`, `UNKNOWN_WORKFLOW_LABEL`) held 15
tasks when this was written: 14 in `done`, 1 in `healed`. None of them are
broken or mis-routed. They are the completed runs of recurring Processes whose
target is a bare step rather than a workflow file — `nanoclaw-sweep`,
`telemetry-anomaly`, `nanoclaw-health` — which is exactly where the projection
is designed to put them.

The problem is not that they arrived. It is that nothing ever takes them away:
a terminal task stays in its column for the lifetime of the root. At roughly
five settled tasks per day the tab grows without bound.

The `development` tab has the same defect in miniature — 1 `done`, 2 `healed`
parked indefinitely — so this is a board-wide gap, not an `unknown`-tab quirk.

Two relevant primitives already exist:

- **`archived/`** — a real `FilesystemTaskQueue` with exactly the semantics
  wanted here: off every board column, still `GET`-able by id, crash-safe.
  `PrWatcher`, `MergeReconciler` and `IssueReconciler` all use it. Nothing
  decides to archive a task for *age*.
- **`POST /tasks/{id}/delete`** — one task at a time, hard discard, no bulk.

What is missing is only the rule that says *when* a settled task should go.

## What it is

A fourth reconciler, `RetentionReconciler`, alongside `MergeReconciler` and
`IssueReconciler`, on the existing 300s reconcile loop:

```
RetentionReconciler(queues=[done, failed, healed], archived=archived,
                    days=2, events=events, clock=clock)
  .tick() -> bool     # True if anything archived
```

Every terminal task, on every tab, moves to `archived/` once `days` have passed
since it settled. Nothing else changes: no new API surface, no template change,
no new orchestration concept.

## Rules

### Scope: terminal columns only

`done`, `failed` and `healed`, across every tab. These are finished tasks;
moving them off the board loses nothing.

Step queues are **never** touched. A task sitting in `plan` for two weeks is
backlog, not garbage — archiving it would silently destroy live work. (At the
time of writing `plan` held 11 such tasks.)

### Age is measured from when the task settled

`history[-1].at` — the entry recording the move into the terminal column — not
`task.created`. A task that ran for three weeks and finished this morning must
stay on the board; keying off `created` would archive it almost the moment it
completed, precisely when the operator most wants to see it.

If `history` is empty, `created` is the fallback. If the timestamp will not
parse, the task is **left in place** — malformed data must not silently vanish
off the board.

### Disposition: archive, never delete

The task moves to `archived/` with `status=ARCHIVED`, keeping its file and its
full history. It stays reachable at `GET /api/tasks/{id}` forever. This is the
identical disposition the three existing reconcilers use, which is why the read
model needs no change (see *Why the UI needs no work*).

### Window: 2 days, from the environment

`HARNESS_RETENTION_DAYS`, read at wiring time in `cli.py` alongside the other
environment reads, defaulting to `2`. Retuning means editing `harness-run.sh`
and restarting — no release.

A value that is unparseable or negative falls back to the default and prints a
non-fatal startup warning to stderr, the same convention
`_warn_missing_autoheal_repository` uses. It must not crash the service: a typo
in a tuning knob is not a reason for the harness to refuse to start.

**Why 2 and not 7.** Seven days was the initial instinct and it is wrong for
this root's volume. The oldest task on the board was three days old, so a 7-day
rule would have archived *nothing* on first tick and then settled the tab at
~35 tasks — more than double the 15 that prompted the request. Two days holds
it at ~10: yesterday's runs still visible in the morning, everything older one
`archived/` lookup away.

## The unit

`src/harness/retention_reconciler.py`, a near-twin of `issue_reconciler.py`.
`tick()` walks each queue's `list()`, computes settled-age against
`clock.now()`, and for anything past the window runs the same archive body
`IssueReconciler._archive` uses:

1. `queue.claim(task, new_lock_id())` — `None` means another actor moved the
   file first; skip it, this is a lost race and not an error.
2. Append `HistoryEntry(actor="retention", from_step=claimed.status,
   to_step=None, reason="retention: settled >Nd ago")`.
3. `replace(status=ARCHIVED, lock_id=None)`.
4. `queue.transfer(resolved, archived)`.
5. `events.emit("archived", task_id=…, queue="archived", task=resolved.to_dict())`.

It knows only ports and models — `TaskQueue`, `EventSink`, `Clock` — and never
a driver, matching the other reconcilers.

## Why the UI needs no work

`ProjectionSink` already maps the `"archived"` event to
`BoardProjection.archive(task)`, which drops the task from every column, keeps
it registered by id, and bumps the revision. The board is SSE-driven off that
revision, so a task archived by the sweep disappears from an open dashboard
without a reload.

`BoardProjection.hydrate` already registers `archived/` contents by id without
placing them in a column, so a restart does not resurrect swept tasks and does
not lose `get()`-ability for them.

This is the whole reason to reuse `archived/` rather than invent a disposition:
the read model, the event stream and the restart path are all already correct.

## Wiring

`app.py`: construct the reconciler next to `issue_reconciler` and host it on a
`_retention_loop` at the existing `reconcile_interval` (300s), the same shape as
`_issue_reconcile_loop`. A sweep that finds nothing is three `list()` calls
against directories holding tens of files — too cheap to justify its own
cadence.

## Testing

`FakeClock` with literal ISO strings and in-memory queues:

- settled 3 days ago → archived; settled 1 day ago → stays
- age reads `history[-1].at`, not `created` — a task created 30 days ago and
  settled today stays on the board
- empty history → falls back to `created`
- unparseable timestamp → left in place, `tick()` continues
- step queues untouched even when their tasks are older than the window
- `claim()` returning `None` → skipped, `tick()` does not raise
- `tick()` returns True only when something was archived
- the emitted `archived` event drives `BoardProjection.archive` — round-trip
  through `ProjectionSink`
- `HARNESS_RETENTION_DAYS` unset → 2; unparseable or negative → 2 with a
  warning

## Deliberately out of scope

No count cap, no per-column windows, no manual bulk-archive button, no
retention policy for `archived/` itself, no un-archive. Each is addable later
and none is needed to stop the growth.

## Expected effect on first tick

Of the 15 tasks in `No workflow`, those settled before ~2026-07-26 archive
immediately — roughly 8–10 of them — leaving the tab around 10 and holding it
there. The `development` tab's 1 `done` and 2 `healed` clear the same way if
they are past the window.
