# Delivery Stats — Design

Status: Draft
Date: 2026-08-02

## Problem

An operator watching the board can see *where every live task is*, and can open
any one task and read its whole history. What they cannot see is the shape of
the last week:

- How many issues did the harness actually implement?
- What kind of issues were they?
- What is the success rate — how often does an automated implementation land,
  and how often does it fail?
- How many pull requests were merged automatically?
- How many merge conflicts arose, and how many did the resolver actually fix?

Today every one of those questions is answered by opening tasks one at a time,
and only for tasks still on the board — the retention sweep archives a settled
task after `HARNESS_RETENTION_DAYS` (default 2), so "the last week" is not
visible at all.

## The key observation

**The harness already records everything needed. Nothing new has to be
measured — only derived.**

Every task file carries `created`, a full `history` of `HistoryEntry`
(timestamp, actor, `from_step`, `to_step`, `outcome`, `summary`, `reason`,
`tokens`) and a `data` blob into which the behaviours already stamp their own
structured output: `data.source` (origin), `data.pr` (landing), `data.merge`
(the `merge-pr` finisher, including `dryRun` and `confidence`), `data.heal`,
`data.tokens_total`.

And nothing deletes task files. `RetentionReconciler`, `MergeReconciler`,
`IssueReconciler` and `PrWatcher` all *move* a task into `archived/`; that
queue has no reader and no pruning. `archived/` is therefore the harness's
long-term record, and it is complete back to the root's first task.

So this feature is a **derivation over data already on disk**, not a new
subsystem. That is the whole design.

### Why not an event log / metrics store

The obvious alternative is a new append-only store fed by an `EventSink`. It is
rejected:

- It would start empty. The operator asked for last week; a store installed
  today answers that question next week.
- It would be a second source of truth for facts the task file already holds,
  and would need its own recovery story, its own retention, its own hydration.
- It buys query speed the harness does not need at its scale (hundreds to low
  thousands of task files).

The trade accepted in exchange: a report costs a scan of the queue directories.
Mitigated by memoisation (below), and revisitable — `StatsView` is a port, so a
store can be swapped in behind it later without the API noticing (ADR-0005).

## Architecture

A **fourth read-only UI surface**, mirroring `StageOutputView`'s introduction
in ADR-0012:

| Surface | Question it answers |
|---|---|
| `BoardView` | where is each task *now* |
| `ArtifactView` | what did a task *produce* |
| `StageOutputView` | what is the running stage doing *right now* |
| **`StatsView`** | **what did the harness deliver over a window** |

```
ports/stats.py     StatsView (ABC) + the report dataclasses
stats.py           summarize(...)  — pure derivation over tasks
                   QueueStatsView  — StatsView over the live TaskQueues
api/               GET /stats (HTML subpage), GET /api/stats?days=N (JSON)
```

`stats.py` sits next to `projection.py` and has exactly its shape: a core module
that reads through the `TaskQueue` port and serves a UI port, importing no
driver. No new driver module is needed — reading queues is a port operation.

`summarize()` is a **pure function** of `(tasks, now, days)`. All the judgement
lives there, testable with plain `Task` literals and no I/O, the same discipline
as `router.route()`.

Wiring: `build()` constructs `QueueStatsView` over the live queues and exposes it
as `harness.stats`; `cli.serve()` hands it to `create_app(stats=...)`, which
falls back to an empty view when none is supplied (the `_EmptyArtifactView`
pattern). `dispatcher.py`/`consumer.py` never import it — guarded by
`test_architecture.py`, mirroring invariants #23/#32/#34.

## Derivations

The delicate part is not the counting, it is deciding **when a task settled and
how it ended**. Two traps:

### Trap 1 — the current status is not the outcome

Once a task is archived, `status` is `archived`, whichever way it ended.
A completed task archived by the retention sweep and a failed task archived
because its issue was closed both read as `archived`. **Terminal disposition
must come from history, never from `status`.**

### Trap 2 — the last history entry is not the settling entry

`RetentionReconciler` and friends *append* an archival entry days after the
task settled. `RetentionReconciler`'s own `settled_at` (the last entry) is
correct for its purpose — it runs before any archival stamp exists — but is
wrong here: it would date a week-old completion to today.

Hence one derivation both problems fall out of:

> **`settling_entry(task)`** — the last `HistoryEntry` whose `to_step` is `end`
> or `failed`.

- Its `at` is the task's **settled time**, used for the window filter.
- `to_step == end` → completed, unless its `actor` is `failed-tasks`, in which
  case the healer retired a failure (ADR-0024) and it counts as a **failure**.
- `to_step == failed` → failed.
- No such entry → the task never settled: **in flight**.

Note this deliberately does *not* reuse `models.is_retired_failure`. That is a
**board** predicate — "is this task, at its current position, a retired
failure" — and requires `status == end` and the healer's entry to be *last*.
Both stop holding the moment the task is archived. Stats needs the
**historical** question, so it reads the settling entry directly. Two predicates
for two genuinely different questions, exactly as ADR-0025 split
`is_retired_failure` from `resumable_failure`.

### Window

A task is in the report when its settled time falls in the last `days` days.
In-flight tasks are counted separately as a point-in-time total, with no window
filter — "12 in flight" is a fact about now, not about the week.

### Kind

Answers "what kind of issues". Derived, in order:

| Kind | Test |
|---|---|
| `heal` | `data.heal` present |
| `conflict` | `data.source.kind == "mergeability"` (`GithubConflictsCheck`) |
| `automerge` | `data.source.kind == "pull-request"` (`GithubMergeableCheck`) |
| `issue` | `data.source.kind` in `{github, jira}` |
| `other` | everything else — `harness submit`, a bare trigger |

`heal` is tested first: a heal task carries no source at all, and grouping it
with `other` would hide the self-healer's volume.

### Success rate

```
success rate = completed / (completed + failed)
```

where `failed` includes healer-retired failures. A retired failure *was* a
failure; the healer filing an issue about it does not make it a delivery. The
report shows retired separately so the split stays visible.

### Delivery and conflict counters

All from `data` stamps and history, over in-window tasks:

- **PRs opened** — `data.pr` present.
- **PRs merged** — a history entry from `merge_reconciler` (`to_step ==
  "archived"`), i.e. the PR was observed merged, by whoever merged it.
- **Auto-merged** — `data.merge` with `dryRun` false; **withheld (dry run)** —
  `data.merge` with `dryRun` true. This pair is the number that says whether it
  is safe to flip `dry_run: false` in the automerge binding.
- **Conflicts** — `kind == conflict`: fired / resolved / failed, plus
  **clean vs. agent-resolved** (below).
- **Rework** — history entries with `outcome == request_changes`, the count of
  review bounces.
- **Cost** — `tokens` on every history entry of an in-window task, summed:
  input, output, `total_cost_usd`.

## Two small stamps this needs

Both are one-liners, and both close a gap where the fact exists but is not
recorded in a machine-readable form.

1. **Issue labels are fetched and thrown away.** `GithubClient.Issue` carries
   `labels`, but neither `github_source.py` nor `github_issues_check.py` stamps
   them onto `data.source` — so "what kind of issue" cannot be answered by the
   issue's own label. Add `"labels"` to the `data.source` both sites build.
   Tasks ingested before this change simply report as unlabelled.

2. **A clean merge and a resolved conflict are distinguishable only in prose.**
   `ResolveConflictBehavior` returns `"merged {base} cleanly, no conflicts"` for
   the no-agent path and the agent's own summary otherwise. Parsing a summary
   string for statistics is exactly the kind of coupling that breaks silently.
   Stamp `data={"resolve": {"clean": true|false}}` instead.

Neither changes routing, and neither is read by `route()` or the dispatcher
(invariant #8).

## The report (MVP)

One page, `/stats`, with a `?days=` selector (1 / 7 / 30 / 90). Report-level:
counters and tables, no charts. A chart is a template change once the
derivation exists, and the derivation is where the value is.

1. **Headline** — settled, completed, failed (of which retired), success rate,
   in flight now.
2. **By kind** — issue / conflict / automerge / heal / other: settled,
   completed, failed, success rate.
3. **By repository** and **by workflow** — the same columns.
4. **By issue label** — issue-kind tasks only.
5. **Where it fails** — failures grouped by the step they died at
   (`failure_trace().failed_step`), most frequent first.
6. **Delivery** — PRs opened, PRs merged, auto-merged, withheld (dry run),
   conflicts fired / resolved clean / resolved by agent / failed, review
   bounces.
7. **Cost** — input/output tokens, USD, and USD per completed task.

`GET /api/stats?days=N` returns the same report as JSON, so the numbers are
scriptable without scraping HTML.

## Cost and caching

A report is a `list()` over `inbox`, every step queue, `done`, `failed`,
`healed` and `archived`, and a parse of each task file. `QueueStatsView`
memoises the last report per `days` value and reuses it for a short TTL, so a
page refresh or a second browser tab does not rescan. The TTL is time-based
rather than revision-based on purpose: `archived/` changes without bumping the
board revision.

## Limits, stated

- **`TaskQueue.list()` does not return claimed tasks.** A task in `.processing/`
  at scan time is invisible to the report. It affects only the in-flight count
  and only for the moment a task is held.
- **Fidelity is bounded by `archived/`.** Nothing prunes it today, so the window
  can be as long as the root is old. If a prune is ever added, the stats window
  must stay inside it — recorded in the ADR so it cannot be forgotten.
- **Cost is attributed by task, not by entry timestamp.** Every history entry of
  an in-window task is summed, including work done before the window opened.
  For a task that settles within days of starting — every kind the harness runs
  — the difference is immaterial.
- **A restarted task reports its latest ending only.** `settling_entry` takes
  the last one. A task that failed, was restarted and then completed counts as
  one completion, which is the operator's question ("did this issue get
  delivered?"), not two events.

## Out of scope

Charts and sparklines; per-agent cost attribution (the data supports it —
`AgentActivity` already exposes per-entry tokens); trend comparison against the
previous window; export; alerting on a falling success rate.
