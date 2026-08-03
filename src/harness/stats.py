"""Delivery statistics, derived from the task record itself.

Sibling of `projection.py` in shape: a core module that reads through the
`TaskQueue` port and serves a UI port, importing no driver. `summarize()` is a
pure function of `(tasks, now, days)` — all the judgement lives there, testable
with plain `Task` literals and no I/O, the same split as `router.route()` and
its callers.

Nothing is stored (ADR-0026). Every number here already exists in the tasks on
disk: the `history` each task carries, plus the structured stamps the behaviours
write (`data.source`, `data.pr`, `data.merge`, `data.resolve`, `data.heal`) and
the token record on each `HistoryEntry` (ADR-0020). `archived/` is never pruned,
so the window can reach back as far as the root is old.

Two derivations carry the whole design, and both exist because the *obvious*
reads are wrong:

- **`status` cannot say how a task ended.** Once archived it is the word
  `archived`, whichever way it went.
- **The last history entry cannot say when it settled.** The reconcilers append
  an archival stamp days later, which would date a week-old completion to today.

So both come from the **settling entry** instead — see `settling_entry`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from harness.merge_reconciler import ACTOR as MERGE_RECONCILER_ACTOR
from harness.models import (
    ARCHIVED,
    END,
    FAILED,
    HEAL_ACTOR,
    REQUEST_CHANGES,
    HistoryEntry,
    Task,
    failure_trace,
)
from harness.ports.clock import Clock
from harness.ports.queue import TaskQueue
from harness.ports.stats import (
    DEFAULT_WINDOW_DAYS,
    KIND_AUTOMERGE,
    KIND_CONFLICT,
    KIND_HEAL,
    KIND_ISSUE,
    KIND_OTHER,
    KINDS,
    Cost,
    Delivery,
    Group,
    Outcomes,
    StatsReport,
    StatsView,
)

CONFLICT_SOURCE_KIND = "mergeability"
"""`drivers/github_conflicts_check.SOURCE_KIND`, repeated rather than imported:
this is a core module and may not import a driver. `tests/test_stats.py` asserts
the two are equal, so a rename in the driver fails the suite instead of silently
reclassifying every resolver task as `other`."""

AUTOMERGE_SOURCE_KIND = "pull-request"
"""`drivers/github_mergeable_check.SOURCE_KIND`. Same arrangement."""

ISSUE_SOURCE_KINDS = frozenset({"github", "jira"})
"""Origins that mean "a human asked for this work". `drivers/github_source.py`
and `drivers/github_issues_check.py` stamp the first, `drivers/jira_issues_check.py`
the second."""

UNSET = "—"
"""Row name for a task carrying no value on the axis being grouped."""

UNLABELLED = "(unlabelled)"
"""Row name for an issue task with no labels — including every task ingested
before labels were stamped onto `data.source` at all."""


def _moment(text: str | None) -> datetime | None:
    """Parse an ISO-8601 harness timestamp, or None if it will not parse.

    The codebase's existing idiom (`retention_reconciler`, `ports/triggers`). A
    naive result is pinned to UTC so a hand-edited task file can never raise on
    comparison with an aware one.
    """
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))  # type: ignore[union-attr]
    except (AttributeError, TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def settling_entry(task: Task) -> HistoryEntry | None:
    """The history entry that ended the task, or None if it never settled.

    The last entry whose `to_step` is `end` or `failed` — the move into a
    terminal column. Everything appended afterwards is an *archival* stamp
    (`retention`, `merge_reconciler`, `issue_reconciler`, `pr_watcher`), which
    records where the task file went, not how the work turned out.

    Both facts the report needs come from this one entry: `at` is when the task
    settled, and `to_step` (plus `actor`) is how it ended. Reading either off
    the task's current `status` or off `history[-1]` gives the wrong answer for
    every archived task, which is most of them once the retention sweep has run.
    """
    for entry in reversed(task.history):
        if entry.to_step in (END, FAILED):
            return entry
    return None


def is_retired(entry: HistoryEntry) -> bool:
    """True when the settling entry is the healer retiring a failure.

    Deliberately not `models.is_retired_failure`: that asks whether a task's
    *current position* is a retired failure, and requires `status == end` with
    the healer's entry last — both stop holding the moment the task is archived.
    This asks the historical question, which survives archival. Two predicates
    over one history, for two questions that genuinely differ (ADR-0026).
    """
    return entry.actor == HEAL_ACTOR and entry.from_step == FAILED


def task_kind(task: Task) -> str:
    """Which of the five kinds of work a task is.

    `heal` is tested first because a heal task carries no `data.source` at all —
    grouping it under `other` would hide the self-healer's whole volume.
    """
    if task.data.get("heal"):
        return KIND_HEAL
    source = task.data.get("source")
    kind = source.get("kind") if isinstance(source, dict) else None
    if kind == CONFLICT_SOURCE_KIND:
        return KIND_CONFLICT
    if kind == AUTOMERGE_SOURCE_KIND:
        return KIND_AUTOMERGE
    if kind in ISSUE_SOURCE_KINDS:
        return KIND_ISSUE
    return KIND_OTHER


def task_labels(task: Task) -> tuple[str, ...]:
    """The source issue's labels, as stamped at ingestion. Empty when the task
    has none, or predates labels being recorded."""
    source = task.data.get("source")
    if not isinstance(source, dict):
        return ()
    labels = source.get("labels")
    if not isinstance(labels, (list, tuple)):
        return ()
    return tuple(str(label) for label in labels)


@dataclass
class _Tally:
    """Mutable accumulator; frozen into `Outcomes` at the end."""

    completed: int = 0
    failed: int = 0
    retired: int = 0

    def add(self, *, completed: bool, retired: bool) -> None:
        if completed:
            self.completed += 1
            return
        # A retired failure is a failure that the healer took over — counted in
        # `failed`, and additionally in `retired` so the split stays visible.
        self.failed += 1
        if retired:
            self.retired += 1

    def freeze(self) -> Outcomes:
        return Outcomes(
            completed=self.completed, failed=self.failed, retired=self.retired
        )


def _groups(tallies: dict[str, _Tally], *, order: Sequence[str] | None = None) -> tuple[Group, ...]:
    """Freeze a tally map into rows.

    `order` fixes the sequence (the fixed kind list); without it rows are sorted
    by volume, then by name so the order is stable when volumes tie.
    """
    if order is not None:
        return tuple(
            Group(name=name, outcomes=tallies[name].freeze())
            for name in order
            if name in tallies
        )
    rows = [Group(name=name, outcomes=tally.freeze()) for name, tally in tallies.items()]
    rows.sort(key=lambda group: (-group.outcomes.settled, group.name))
    return tuple(rows)


@dataclass
class _DeliveryTally:
    prs_opened: int = 0
    prs_merged: int = 0
    auto_merged: int = 0
    merge_withheld: int = 0
    conflicts: int = 0
    conflicts_clean: int = 0
    conflicts_resolved: int = 0
    conflicts_failed: int = 0
    review_bounces: int = 0

    def freeze(self) -> Delivery:
        return Delivery(**vars(self))


def _count_delivery(task: Task, *, kind: str, completed: bool, into: _DeliveryTally) -> None:
    """Fold one settled task's delivery facts into the tally."""
    if isinstance(task.data.get("pr"), dict):
        into.prs_opened += 1
    if any(entry.actor == MERGE_RECONCILER_ACTOR for entry in task.history):
        # The reconciler only ever appends this entry after observing the PR
        # merged — by whoever merged it, harness or human.
        into.prs_merged += 1

    merge = task.data.get("merge")
    if isinstance(merge, dict):
        if merge.get("dryRun"):
            into.merge_withheld += 1
        else:
            into.auto_merged += 1

    if kind == KIND_CONFLICT:
        into.conflicts += 1
        if not completed:
            into.conflicts_failed += 1
        else:
            resolve = task.data.get("resolve")
            clean = resolve.get("clean") if isinstance(resolve, dict) else None
            # An unstamped task (one that ran before `data.resolve` existed)
            # counts as resolved rather than clean: it is the conservative read,
            # since a clean merge is the cheaper half of the pair.
            if clean:
                into.conflicts_clean += 1
            else:
                into.conflicts_resolved += 1

    into.review_bounces += sum(
        1 for entry in task.history if entry.outcome == REQUEST_CHANGES
    )


@dataclass
class _CostTally:
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0

    def add(self, task: Task) -> None:
        for entry in task.history:
            tokens = entry.tokens
            if not isinstance(tokens, dict):
                continue
            self.input_tokens += _as_int(tokens.get("input"))
            self.output_tokens += _as_int(tokens.get("output"))
            self.usd += _as_float(tokens.get("total_cost_usd"))

    def freeze(self) -> Cost:
        return Cost(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            usd=round(self.usd, 4),
        )


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_float(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def summarize(
    tasks: Iterable[Task], *, now: str, days: int = DEFAULT_WINDOW_DAYS
) -> StatsReport:
    """Everything the stats page shows, derived from a task list. Pure.

    A task is in the window when it *settled* within the last `days` days. One
    that never settled is counted as in flight instead — a point-in-time total,
    deliberately not window-filtered, unless it was archived mid-flight (the
    issue closed out from under it), in which case it is neither.
    """
    moment = _moment(now)
    cutoff = moment - timedelta(days=days) if moment else None

    overall = _Tally()
    by_kind: dict[str, _Tally] = {}
    by_repository: dict[str, _Tally] = {}
    by_workflow: dict[str, _Tally] = {}
    by_label: dict[str, _Tally] = {}
    failures: Counter[str] = Counter()
    delivery = _DeliveryTally()
    cost = _CostTally()
    in_flight = 0

    for task in tasks:
        entry = settling_entry(task)
        if entry is None:
            # Never settled. Archived anyway means the issue closed out from
            # under it (IssueReconciler) — gone, not in flight.
            if task.status != ARCHIVED:
                in_flight += 1
            continue

        at = _moment(entry.at)
        if cutoff is not None and (at is None or at < cutoff):
            continue

        completed = entry.to_step == END and not is_retired(entry)
        retired = entry.to_step == END and not completed
        kind = task_kind(task)

        for bucket, name in (
            (by_kind, kind),
            (by_repository, task.repository or UNSET),
            (by_workflow, task.workflow_template or UNSET),
        ):
            bucket.setdefault(name, _Tally()).add(completed=completed, retired=retired)
        overall.add(completed=completed, retired=retired)

        if kind == KIND_ISSUE:
            for label in task_labels(task) or (UNLABELLED,):
                by_label.setdefault(label, _Tally()).add(
                    completed=completed, retired=retired
                )

        if not completed:
            trace = failure_trace(task)
            failures[trace.failed_step if trace else UNSET] += 1

        _count_delivery(task, kind=kind, completed=completed, into=delivery)
        cost.add(task)

    ranked = sorted(failures.items(), key=lambda item: (-item[1], item[0]))
    return StatsReport(
        window_days=days,
        generated_at=now,
        overall=overall.freeze(),
        in_flight=in_flight,
        by_kind=_groups(by_kind, order=KINDS),
        by_repository=_groups(by_repository),
        by_workflow=_groups(by_workflow),
        by_label=_groups(by_label),
        failures_by_step=tuple(ranked),
        delivery=delivery.freeze(),
        cost=cost.freeze(),
    )


class QueueStatsView(StatsView):
    """`StatsView` over the harness's own queues.

    Lists every queue that can hold a task — including `archived/`, which is
    where most of the window lives once the retention sweep has run — and hands
    the result to `summarize`. Knows only ports; no driver, no filesystem.

    Reports are memoised per window for `ttl_seconds`, so a page refresh or a
    second browser tab does not rescan. The TTL is time-based rather than keyed
    on the board revision on purpose: `archived/` changes without bumping it.
    """

    def __init__(
        self,
        *,
        queues: Sequence[TaskQueue],
        clock: Clock,
        ttl_seconds: float = 5.0,
    ) -> None:
        self._queues = list(queues)
        self._clock = clock
        self._ttl = ttl_seconds
        self._cache: dict[int, tuple[datetime | None, StatsReport]] = {}

    def report(self, days: int) -> StatsReport:
        now = self._clock.now()
        moment = _moment(now)
        cached = self._cache.get(days)
        if cached is not None and moment is not None:
            generated, report = cached
            if generated is not None and 0 <= (moment - generated).total_seconds() < self._ttl:
                return report

        tasks = [task for queue in self._queues for task in queue.list()]
        report = summarize(tasks, now=now, days=days)
        self._cache[days] = (moment, report)
        return report
