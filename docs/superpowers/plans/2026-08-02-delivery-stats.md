# Delivery Stats Implementation Plan

**Goal:** Give the operator a `/stats` subpage answering "what did the harness
deliver over the last N days" — throughput, success rate, failure hotspots,
auto-merges, conflict resolution and cost — derived entirely from tasks already
on disk.

**Architecture:** A pure derivation (`stats.summarize`) over `Task` records,
served through a new read-only port (`StatsView`) by a core module
(`QueueStatsView`) that reads the live `TaskQueue`s. Report-level UI: counters
and tables, no charts. See ADR-0026 and the spec.

**Tech Stack:** Python 3.11, FastAPI + Jinja2, pytest. No new dependencies.

## Global Constraints

- **Project language is English — always.**
- **Commit straight into the designated feature branch** for this change
  (`claude/harness-dev-stats-feature-yo1fvt`), conventional commits.
- **`dispatcher.py`/`consumer.py` must not import `ports/stats.py`** — add the
  guard to `tests/test_architecture.py`.
- **`api/` imports no driver** — it sees `StatsView` only.
- **`summarize()` is pure**: no I/O, no clock, no queue. `now` is a parameter.
- **No test may sleep in real time.** Use `FakeClock`.
- **Nothing here may influence routing** (invariant #8): the two new `data`
  stamps are record-only.

Spec: `docs/superpowers/specs/2026-08-02-delivery-stats-design.md`
ADR: `docs/adr/0026-stats-are-derived-never-stored.md`

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/harness/ports/stats.py` | `StatsView` ABC + report dataclasses | 1 |
| `src/harness/stats.py` | `settling_entry`, `summarize` (pure), `QueueStatsView` | 2 |
| `src/harness/drivers/github_source.py` | stamp `labels` onto `data.source` | 3 |
| `src/harness/drivers/github_issues_check.py` | stamp `labels` onto `data.source` | 3 |
| `src/harness/behaviors/resolve_conflict.py` | stamp `data.resolve.clean` | 3 |
| `src/harness/app.py` | build `QueueStatsView`, expose `harness.stats` | 4 |
| `src/harness/api/app.py` | `_EmptyStatsView`, `stats=` parameter | 5 |
| `src/harness/api/routes.py` | `GET /stats`, `GET /api/stats` | 5 |
| `src/harness/api/templates/stats.html` | the report page | 5 |
| `src/harness/api/templates/_nav.html` | the `Stats` nav entry | 5 |
| `src/harness/api/static/app.css` | stat-tile / report-table styles | 5 |
| `src/harness/cli.py` | pass `harness.stats` into `create_app` | 6 |
| `tests/test_stats.py` | the derivation's tests | 2 |
| `tests/test_architecture.py` | orchestration does not import the stats port | 2 |
| `CLAUDE.md` | invariant #45, module map | 7 |

Tasks 1→2 are ordered; 3 is independent; 4→5→6 follow 2.

---

### Task 1: The port

`ports/stats.py`, shaped like `ports/board.py`: the dataclasses and the ABC
together.

- `StatKind` constants: `KIND_ISSUE`, `KIND_CONFLICT`, `KIND_AUTOMERGE`,
  `KIND_HEAL`, `KIND_OTHER`.
- `Outcomes` — `completed`, `failed`, `retired` counts, plus `settled` and
  `success_rate` properties.
- `Group` — a named row: `name` + `Outcomes`.
- `Delivery` — `prs_opened`, `prs_merged`, `auto_merged`, `merge_withheld`,
  `conflicts`, `conflicts_clean`, `conflicts_resolved`, `conflicts_failed`,
  `review_bounces`.
- `Cost` — `input_tokens`, `output_tokens`, `usd`, plus `usd_per_completed`.
- `StatsReport` — `window_days`, `generated_at`, `in_flight`, `overall:
  Outcomes`, `by_kind`, `by_repository`, `by_workflow`, `by_label`,
  `failures_by_step` (tuple of `(step, count)`), `delivery`, `cost`; and
  `to_dict()` for the JSON endpoint.
- `StatsView(ABC)` — `report(days: int) -> StatsReport`.

### Task 2: The derivation

`src/harness/stats.py`, next to `projection.py`.

- `settling_entry(task) -> HistoryEntry | None` — last entry with `to_step` in
  `(END, FAILED)`. The spec's trap 1 and 2 both fall out of this.
- `task_kind(task) -> str` — `heal` (via `data.heal`) first, then the two
  PR-born source kinds, then `github`/`jira`, else `other`.
- `summarize(tasks, *, now, days) -> StatsReport` — pure.
- `QueueStatsView(StatsView)` — takes the queues, memoises per `days` for a TTL,
  calls `summarize`.

Tests cover: window filtering off the *settling* entry not the last entry; an
archived completion still reading as completed; a retired failure counted as a
failure and reported separately; kind classification; success rate;
failures-by-step; delivery counters; cost; empty input.

Add to `tests/test_architecture.py`:
`test_orchestration_does_not_import_stats_port`.

### Task 3: The two stamps

- `github_source.py` / `github_issues_check.py`: `"labels": list(issue.labels)`
  inside the `data.source` dict.
- `resolve_conflict.py`: `data={"resolve": {"clean": True}}` on the clean-merge
  return, `{"clean": False}` on the agent path.

### Task 4: Wiring in `build()`

Construct `QueueStatsView` over `inbox`, the step queues, `done`, `failed`,
`healed_queue` and `archived`; pass to `Harness(stats=...)`; expose as
`harness.stats`. Always built — it needs no external service, like
`RetentionReconciler`.

### Task 5: The API and the page

- `create_app(stats=None)` with an `_EmptyStatsView` fallback.
- `GET /api/stats?days=7` → `report.to_dict()`.
- `GET /stats?days=7` → `stats.html`.
- `days` clamped to a sane range; unparseable falls back to 7.
- Nav entry between Board and Agents.

### Task 6: `cli.serve()`

Pass `stats=harness.stats` into `create_app`.

### Task 7: Documentation

`CLAUDE.md`: new invariant #45 (stats are derived, never stored; the settling
entry is the source of both the outcome and the settled time), the module map
rows, and a gotcha about `archived/` bounding the window.
