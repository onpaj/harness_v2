"""The port the UI looks through for delivery statistics.

A fourth read surface next to `BoardView`/`ArtifactView`/`StageOutputView`: the
board shows *where* a task is, the artifacts *what it produced*, the stage
output *what it is doing right now* — this shows *what the harness delivered
over a window*. Read-only and driver-agnostic; today a derivation over the task
files already on disk (ADR-0026), tomorrow whatever materialised read model
replaces it.

The dataclasses live here with the ABC, exactly as `Board`/`BoardColumn` live
with `BoardView` in `ports/board.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

DEFAULT_WINDOW_DAYS = 7
"""The window a report covers when none is asked for."""

MAX_WINDOW_DAYS = 3650
"""A decade. There is no "everything" window — a number keeps the report's
header honest about what it covered."""


def clamp_window(days: Any, *, default: int = DEFAULT_WINDOW_DAYS) -> int:
    """A usable window from whatever a query string carried.

    Unparseable or non-positive falls back to the default rather than erroring:
    a hand-typed `?days=` is not worth a 422, and the report states the window
    it actually used. Lives with the port because it is the UI's half of the
    contract — `api/` reads only this module.
    """
    try:
        parsed = int(days)
    except (TypeError, ValueError):
        return default
    if parsed < 1:
        return default
    return min(parsed, MAX_WINDOW_DAYS)


KIND_ISSUE = "issue"
"""Work born from an issue in a tracker — the harness implementing something."""

KIND_CONFLICT = "conflict"
"""A resolver task: `GithubConflictsCheck` saw a PR go `dirty`."""

KIND_AUTOMERGE = "automerge"
"""An automerge-review task: `GithubMergeableCheck` saw a PR go `clean`."""

KIND_HEAL = "heal"
"""A self-healing task the `failed-tasks` check fired for a failure."""

KIND_OTHER = "other"
"""Everything else — `harness submit`, a bare scheduled trigger."""

KINDS = (KIND_ISSUE, KIND_CONFLICT, KIND_AUTOMERGE, KIND_HEAL, KIND_OTHER)
"""Report order. Issues first: they are what the operator came to look at."""


@dataclass(frozen=True)
class Outcomes:
    """How a set of settled tasks ended.

    `retired` is a subset of `failed`, not a third disposition: a healer-retired
    failure lands in `done/` (ADR-0024) but it *was* a failure, and the healer
    filing an issue about it is not a delivery. It is counted separately so the
    split stays visible, never added on top.
    """

    completed: int = 0
    failed: int = 0
    retired: int = 0

    @property
    def settled(self) -> int:
        return self.completed + self.failed

    @property
    def success_rate(self) -> float | None:
        """Share of settled tasks that completed, or None when nothing settled.

        None rather than 0.0 — "no tasks" and "every task failed" are different
        facts and the report must not render them identically.
        """
        return self.completed / self.settled if self.settled else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "completed": self.completed,
            "failed": self.failed,
            "retired": self.retired,
            "settled": self.settled,
            "successRate": self.success_rate,
        }


@dataclass(frozen=True)
class Group:
    """One row of a breakdown table: a name and how its tasks ended."""

    name: str
    outcomes: Outcomes

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, **self.outcomes.to_dict()}


@dataclass(frozen=True)
class Delivery:
    """What reached a pull request, and what happened to it there."""

    prs_opened: int = 0
    prs_merged: int = 0
    """Observed merged by `MergeReconciler` — by whoever merged it, harness or
    human. Never inferred from the automerge counters below."""
    auto_merged: int = 0
    """Merged by the harness itself, through `PullRequestMerger` (ADR-0023)."""
    merge_withheld: int = 0
    """The `merge-pr` finisher would have merged but `dry_run` was on. Paired
    with `auto_merged`, this is the number that says whether it is safe to flip
    `dry_run: false`."""
    conflicts: int = 0
    conflicts_clean: int = 0
    """Resolved by a plain merge, no agent call spent."""
    conflicts_resolved: int = 0
    """Resolved by the `resolve` persona — a real conflict, really fixed."""
    conflicts_failed: int = 0
    review_bounces: int = 0
    """`request_changes` outcomes — how much rework the review loop demanded."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "prsOpened": self.prs_opened,
            "prsMerged": self.prs_merged,
            "autoMerged": self.auto_merged,
            "mergeWithheld": self.merge_withheld,
            "conflicts": self.conflicts,
            "conflictsClean": self.conflicts_clean,
            "conflictsResolved": self.conflicts_resolved,
            "conflictsFailed": self.conflicts_failed,
            "reviewBounces": self.review_bounces,
        }


@dataclass(frozen=True)
class Cost:
    """What the window cost in tokens and money.

    Summed from the `tokens` record on every history entry of an in-window task
    (`BehaviorResult.tokens`, ADR-0020) — so it is attributed by task, not by
    entry timestamp. Best-effort by nature: an agent run that reported no usage
    contributes nothing.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0

    def usd_per_completed(self, completed: int) -> float | None:
        return self.usd / completed if completed else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "usd": self.usd,
        }


@dataclass(frozen=True)
class StatsReport:
    """Everything the stats page renders, for one window."""

    window_days: int
    generated_at: str
    overall: Outcomes = field(default_factory=Outcomes)
    in_flight: int = 0
    """Point-in-time, deliberately *not* window-filtered: "9 in flight" is a
    fact about now, not about the last week."""
    by_kind: tuple[Group, ...] = ()
    by_repository: tuple[Group, ...] = ()
    by_workflow: tuple[Group, ...] = ()
    by_label: tuple[Group, ...] = ()
    failures_by_step: tuple[tuple[str, int], ...] = ()
    delivery: Delivery = field(default_factory=Delivery)
    cost: Cost = field(default_factory=Cost)

    def to_dict(self) -> dict[str, Any]:
        return {
            "windowDays": self.window_days,
            "generatedAt": self.generated_at,
            "overall": self.overall.to_dict(),
            "inFlight": self.in_flight,
            "byKind": [group.to_dict() for group in self.by_kind],
            "byRepository": [group.to_dict() for group in self.by_repository],
            "byWorkflow": [group.to_dict() for group in self.by_workflow],
            "byLabel": [group.to_dict() for group in self.by_label],
            "failuresByStep": [
                {"step": step, "count": count} for step, count in self.failures_by_step
            ],
            "delivery": self.delivery.to_dict(),
            "cost": {
                **self.cost.to_dict(),
                "usdPerCompleted": self.cost.usd_per_completed(self.overall.completed),
            },
        }


class StatsView(ABC):
    """Read-only view of what the harness delivered."""

    @abstractmethod
    def report(self, days: int) -> StatsReport:
        """The report for the last `days` days."""
