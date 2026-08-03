# ADR-0026: Delivery stats are derived from the task record, never stored

Status: Accepted

## Context

The board answers "where is each task now". It cannot answer "what did the
harness deliver last week" — how many issues were implemented, how many
succeeded, how many pull requests merged themselves, how many conflicts the
resolver actually fixed. Worse, it *cannot* be made to: `RetentionReconciler`
archives a settled task after two days, so the week is off the board by design.

The reflex answer is a metrics store — an `EventSink` appending to a log, or a
counter table updated as tasks move.

Two facts make that the wrong shape here:

1. **The record already exists and is complete.** Every task file carries its
   whole `history` — timestamp, actor, from/to step, outcome, reason and token
   usage per handling — plus the structured stamps the behaviours already write
   (`data.source`, `data.pr`, `data.merge`, `data.heal`, `data.tokens_total`).
2. **Nothing is ever deleted.** `PrWatcher`, `MergeReconciler`,
   `IssueReconciler` and `RetentionReconciler` all *transfer* into `archived/`.
   That queue has no reader and no pruning; it is the long-term record, intact
   back to the root's first task.

A store would therefore duplicate a source of truth, need its own recovery and
retention story — and start empty, answering next week the question asked today.

## Decision

**Statistics are a pure derivation over the tasks already on disk, behind a
read-only port.**

- **`StatsView` is a fourth UI read surface**, alongside `BoardView`,
  `ArtifactView` and `StageOutputView` (ADR-0012). `api/` sees only the port
  (ADR-0005); `dispatcher.py`/`consumer.py` never import it, guarded by
  `test_architecture.py` like every other non-orchestration port.
- **The judgement is a pure function.** `stats.summarize(tasks, now, days)`
  takes a task list and returns a report — no I/O, no clock of its own, no
  queue. `QueueStatsView` is the thin part: list the queues, call the function.
  Same split as `router.route()` and its callers.
- **Terminal disposition comes from history, never from `status`.** By the time
  a task is archived, `status` is `archived` whichever way it ended, so
  `status` cannot answer "did this succeed". The report reads the **settling
  entry** — the last `HistoryEntry` whose `to_step` is `end` or `failed` — for
  both the outcome and the moment it settled.
- **A healer-retired failure counts as a failure.** ADR-0024 lands it in
  `done/`; the healer filing an issue about a failure does not make it a
  delivery. It is reported separately so the split stays visible.
- **Where a fact exists but only as prose, stamp it.** Two additions, both
  routing-inert (invariant #8): issue `labels` onto `data.source` at both GitHub
  ingestion sites, and `data.resolve.clean` from `ResolveConflictBehavior`.
  Deriving statistics by pattern-matching a `summary` string would couple the
  report to wording that nobody thinks of as an interface.

## Consequences

- **The feature reports on history that already happened.** The first render
  covers the whole life of the root, not the time since deployment. This is the
  entire payoff of the decision and the reason a store loses.
- **A report costs a directory scan.** Every live queue plus `archived/`, one
  parse per task file, memoised for a short TTL. Fine at hundreds-to-thousands
  of tasks; if it ever stops being fine, `StatsView` is a port and a materialised
  read model can be swapped in behind it without `api/` noticing.
- **`archived/` is now load-bearing for a second reason.** It was "keep the
  record gettable by id"; it is now also the statistics window. **If pruning of
  `archived/` is ever added, the stats window must stay inside it** — and the
  report would silently get shorter rather than fail, which is the failure mode
  to watch for.
- **`is_retired_failure` did not become the stats predicate.** It asks whether a
  task's *current position* is a retired failure and requires the healer's entry
  to be last — both false once the task is archived. Stats asks the historical
  question instead. Two predicates over the same history, for two questions that
  genuinely differ, exactly as ADR-0025 split `is_retired_failure` from
  `resumable_failure`.
- **A claimed task is invisible to a report.** `TaskQueue.list()` returns
  unclaimed tasks only, so a task held in `.processing/` at scan time is missed.
  It affects the in-flight count for the duration of one handling, and nothing
  in the settled window.
