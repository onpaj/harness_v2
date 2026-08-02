"""Tests for the delivery report's derivation (ADR-0026).

`summarize` is pure, so every test here is a task literal in and a report out —
no queues, no clock, no disk.
"""

from __future__ import annotations

import pytest

from harness.drivers.github_conflicts_check import SOURCE_KIND as CONFLICTS_KIND
from harness.drivers.github_mergeable_check import SOURCE_KIND as MERGEABLE_KIND
from harness.drivers.memory import FakeClock, MemoryTaskQueue
from harness.models import (
    ARCHIVED,
    END,
    FAILED,
    HEAL_ACTOR,
    REQUEST_CHANGES,
    HistoryEntry,
    Task,
)
from harness.ports.stats import (
    KIND_AUTOMERGE,
    KIND_CONFLICT,
    KIND_HEAL,
    KIND_ISSUE,
    KIND_OTHER,
    clamp_window,
)
from harness.stats import (
    AUTOMERGE_SOURCE_KIND,
    CONFLICT_SOURCE_KIND,
    QueueStatsView,
    settling_entry,
    summarize,
    task_kind,
)

NOW = "2026-08-02T12:00:00Z"


def entry(
    *, at: str, to: str | None, frm: str | None = "development", actor: str = "dispatcher", **kw
) -> HistoryEntry:
    return HistoryEntry(at=at, actor=actor, from_step=frm, to_step=to, **kw)


def task(
    task_id: str = "t1",
    *,
    history: tuple[HistoryEntry, ...] = (),
    status: str | None = None,
    **kw,
) -> Task:
    return Task(
        id=task_id, created="2026-08-01T09:00:00Z", history=history, status=status, **kw
    )


def issue_task(task_id: str, *, at: str, to: str = END, labels=(), **kw) -> Task:
    """A github-issue task that settled at `at`."""
    data = {"source": {"kind": "github", "repo": "onpaj/harness_v2", "issue": 1,
                       "labels": list(labels)}}
    data.update(kw.pop("data", {}))
    return task(
        task_id,
        history=kw.pop("history", (entry(at=at, to=to),)),
        status=END if to == END else FAILED,
        workflow_template="development",
        repository="harness_v2",
        data=data,
        **kw,
    )


def group(rows, name):
    return next((row for row in rows if row.name == name), None)


# --- the two traps the whole design exists for -------------------------------


def test_settling_entry_ignores_the_archival_stamp_appended_later():
    """The last entry is the archival stamp; the settling entry is the move
    into a terminal column, days earlier."""
    settled = entry(at="2026-07-20T10:00:00Z", to=END)
    archived = entry(
        at="2026-08-02T11:00:00Z", to="archived", frm=END, actor="retention"
    )
    assert settling_entry(task(history=(settled, archived))) is settled


def test_an_archived_completion_still_reads_as_completed():
    """`status` is `archived` whichever way the task ended, so the report must
    never read it."""
    completed = task(
        history=(
            entry(at=NOW, to=END),
            entry(at=NOW, to="archived", frm=END, actor="retention"),
        ),
        status=ARCHIVED,
    )
    report = summarize([completed], now=NOW, days=7)
    assert (report.overall.completed, report.overall.failed) == (1, 0)


def test_window_is_measured_from_settling_not_from_archival():
    """A task that settled five weeks ago but was archived today is out of a
    7-day window — the bug that reading `history[-1]` would cause."""
    old = task(
        history=(
            entry(at="2026-06-25T10:00:00Z", to=END),
            entry(at=NOW, to="archived", frm=END, actor="retention"),
        ),
        status=ARCHIVED,
    )
    assert summarize([old], now=NOW, days=7).overall.settled == 0
    assert summarize([old], now=NOW, days=90).overall.settled == 1


# --- dispositions ------------------------------------------------------------


def test_a_retired_failure_counts_as_a_failure_and_is_reported_separately():
    retired = task(
        history=(
            entry(at=NOW, to=FAILED, frm="development"),
            entry(at=NOW, to=END, frm=FAILED, actor=HEAL_ACTOR),
        ),
        status=END,
    )
    report = summarize([retired], now=NOW, days=7)
    assert report.overall.completed == 0
    assert report.overall.failed == 1
    assert report.overall.retired == 1
    # Retired is a subset of failed, never an extra settled task.
    assert report.overall.settled == 1


def test_a_task_that_never_settled_is_in_flight_not_settled():
    running = task(history=(entry(at=NOW, to="development", frm=None),), status="development")
    report = summarize([running], now=NOW, days=7)
    assert (report.in_flight, report.overall.settled) == (1, 0)


def test_a_task_archived_mid_flight_is_neither_settled_nor_in_flight():
    """The issue closed out from under it (IssueReconciler) — it is gone, not
    working."""
    abandoned = task(
        history=(entry(at=NOW, to=None, frm="development", actor="issue_reconciler"),),
        status=ARCHIVED,
    )
    report = summarize([abandoned], now=NOW, days=7)
    assert (report.in_flight, report.overall.settled) == (0, 0)


def test_in_flight_ignores_the_window():
    """A long-running task is a fact about now, not about the last day."""
    running = task(history=(entry(at="2026-01-01T00:00:00Z", to="development", frm=None),))
    assert summarize([running], now=NOW, days=1).in_flight == 1


def test_the_latest_ending_wins_for_a_restarted_task():
    restarted = task(
        history=(
            entry(at="2026-07-30T10:00:00Z", to=FAILED),
            entry(at=NOW, to=END),
        ),
        status=END,
    )
    report = summarize([restarted], now=NOW, days=7)
    assert (report.overall.completed, report.overall.failed) == (1, 0)


def test_success_rate_is_none_when_nothing_settled():
    """None, not 0.0 — "nothing ran" must not render like "everything failed"."""
    assert summarize([], now=NOW, days=7).overall.success_rate is None


def test_success_rate_counts_retired_failures_against_it():
    tasks = [
        issue_task("a", at=NOW),
        issue_task("b", at=NOW),
        issue_task("c", at=NOW, to=FAILED),
        task(
            "d",
            history=(
                entry(at=NOW, to=FAILED),
                entry(at=NOW, to=END, frm=FAILED, actor=HEAL_ACTOR),
            ),
            status=END,
        ),
    ]
    assert summarize(tasks, now=NOW, days=7).overall.success_rate == 0.5


# --- classification ----------------------------------------------------------


def test_source_kind_literals_match_the_drivers_that_stamp_them():
    """`stats.py` is core and may not import a driver, so it repeats these two
    literals. This is the guard that a rename in either driver fails loudly
    instead of silently reclassifying every task as `other`."""
    assert CONFLICT_SOURCE_KIND == CONFLICTS_KIND
    assert AUTOMERGE_SOURCE_KIND == MERGEABLE_KIND


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"source": {"kind": "github"}}, KIND_ISSUE),
        ({"source": {"kind": "jira"}}, KIND_ISSUE),
        ({"source": {"kind": CONFLICTS_KIND}}, KIND_CONFLICT),
        ({"source": {"kind": MERGEABLE_KIND}}, KIND_AUTOMERGE),
        ({}, KIND_OTHER),
        ({"source": {"kind": "something-else"}}, KIND_OTHER),
        # A heal task carries no source at all; `other` would hide the whole
        # self-healer, so `data.heal` is tested first.
        ({"heal": {"task": "t0"}}, KIND_HEAL),
        ({"heal": {"task": "t0"}, "source": {"kind": "github"}}, KIND_HEAL),
    ],
)
def test_task_kind(data, expected):
    assert task_kind(task(data=data)) == expected


def test_breakdowns_group_by_kind_repository_and_workflow():
    tasks = [
        issue_task("a", at=NOW),
        issue_task("b", at=NOW, to=FAILED),
        task(
            "c",
            history=(entry(at=NOW, to=END),),
            status=END,
            repository="other_repo",
            workflow_template="resolver",
            data={"source": {"kind": CONFLICTS_KIND}},
        ),
    ]
    report = summarize(tasks, now=NOW, days=7)

    assert group(report.by_kind, KIND_ISSUE).outcomes.settled == 2
    assert group(report.by_kind, KIND_CONFLICT).outcomes.completed == 1
    assert group(report.by_repository, "harness_v2").outcomes.failed == 1
    assert group(report.by_workflow, "resolver").outcomes.completed == 1
    # Fixed order for kinds, volume order for the open-ended axes.
    assert [row.name for row in report.by_kind] == [KIND_ISSUE, KIND_CONFLICT]
    assert [row.name for row in report.by_repository] == ["harness_v2", "other_repo"]


def test_labels_break_down_issue_tasks_and_unlabelled_is_its_own_row():
    tasks = [
        issue_task("a", at=NOW, labels=["bug", "ui"]),
        issue_task("b", at=NOW, to=FAILED, labels=["bug"]),
        issue_task("c", at=NOW),  # ingested before labels were recorded
    ]
    report = summarize(tasks, now=NOW, days=7)
    assert group(report.by_label, "bug").outcomes.settled == 2
    assert group(report.by_label, "ui").outcomes.completed == 1
    assert group(report.by_label, "(unlabelled)").outcomes.completed == 1


def test_labels_of_a_non_issue_task_are_not_a_label_row():
    conflict = task(
        history=(entry(at=NOW, to=END),),
        status=END,
        data={"source": {"kind": CONFLICTS_KIND, "labels": ["noise"]}},
    )
    assert summarize([conflict], now=NOW, days=7).by_label == ()


# --- failure hotspots --------------------------------------------------------


def test_failures_are_grouped_by_the_step_they_died_at_most_frequent_first():
    def failed_at(task_id, step):
        return task(
            task_id,
            history=(
                entry(at=NOW, to=step, frm=None),
                entry(at=NOW, to=FAILED, frm=step, reason="boom"),
            ),
            status=FAILED,
        )

    report = summarize(
        [failed_at("a", "verify"), failed_at("b", "verify"), failed_at("c", "land")],
        now=NOW,
        days=7,
    )
    assert report.failures_by_step == (("verify", 2), ("land", 1))


# --- delivery ----------------------------------------------------------------


def test_delivery_counts_prs_merges_and_review_bounces():
    landed = issue_task("a", at=NOW, data={"pr": {"url": "u", "number": 1}})
    merged = task(
        "b",
        history=(
            entry(at=NOW, to=END),
            entry(at=NOW, to="archived", frm=END, actor="merge_reconciler"),
        ),
        status=ARCHIVED,
        data={"pr": {"url": "u2", "number": 2}},
    )
    bounced = issue_task(
        "c",
        at=NOW,
        history=(
            entry(at=NOW, to="development", outcome=REQUEST_CHANGES),
            entry(at=NOW, to=END),
        ),
    )
    report = summarize([landed, merged, bounced], now=NOW, days=7)
    assert report.delivery.prs_opened == 2
    assert report.delivery.prs_merged == 1
    assert report.delivery.review_bounces == 1


def test_automerge_separates_a_real_merge_from_a_dry_run():
    def automerge(task_id, dry_run):
        return task(
            task_id,
            history=(entry(at=NOW, to=END),),
            status=END,
            data={
                "source": {"kind": MERGEABLE_KIND},
                "merge": {"dryRun": dry_run, "confidence": 0.9},
            },
        )

    report = summarize([automerge("a", True), automerge("b", False)], now=NOW, days=7)
    assert (report.delivery.auto_merged, report.delivery.merge_withheld) == (1, 1)


def test_conflicts_split_clean_resolved_and_failed():
    def conflict(task_id, *, to=END, resolve=None):
        data = {"source": {"kind": CONFLICTS_KIND}}
        if resolve is not None:
            data["resolve"] = resolve
        return task(
            task_id,
            history=(
                entry(at=NOW, to="resolve", frm=None),
                entry(at=NOW, to=to, frm="resolve"),
            ),
            status=END if to == END else FAILED,
            data=data,
        )

    report = summarize(
        [
            conflict("a", resolve={"clean": True}),
            conflict("b", resolve={"clean": False}),
            conflict("c", to=FAILED),
            conflict("d"),  # ran before `data.resolve` existed
        ],
        now=NOW,
        days=7,
    )
    assert report.delivery.conflicts == 4
    assert report.delivery.conflicts_clean == 1
    # The unstamped one reads as agent-resolved: the conservative half.
    assert report.delivery.conflicts_resolved == 2
    assert report.delivery.conflicts_failed == 1


# --- cost --------------------------------------------------------------------


def test_cost_sums_every_token_record_of_an_in_window_task():
    tokens = {"input": 1000, "output": 200, "total_cost_usd": 0.25, "model": "opus"}
    spent = issue_task(
        "a",
        at=NOW,
        history=(
            entry(at=NOW, to="development", tokens=tokens),
            entry(at=NOW, to=END, tokens=tokens),
        ),
    )
    report = summarize([spent], now=NOW, days=7)
    assert (report.cost.input_tokens, report.cost.output_tokens) == (2000, 400)
    assert report.cost.usd == pytest.approx(0.5)
    assert report.cost.usd_per_completed(report.overall.completed) == pytest.approx(0.5)


def test_cost_tolerates_entries_with_no_token_record():
    plain = issue_task("a", at=NOW)
    report = summarize([plain], now=NOW, days=7)
    assert (report.cost.input_tokens, report.cost.usd) == (0, 0.0)
    assert report.cost.usd_per_completed(0) is None


# --- edges -------------------------------------------------------------------


def test_an_empty_harness_reports_zeroes_not_an_error():
    report = summarize([], now=NOW, days=7)
    assert report.overall.settled == 0
    assert report.to_dict()["overall"]["successRate"] is None


def test_an_unparseable_settling_timestamp_is_excluded_not_fatal():
    broken = task(history=(entry(at="not-a-date", to=END),), status=END)
    assert summarize([broken], now=NOW, days=7).overall.settled == 0


@pytest.mark.parametrize(
    "raw,expected", [("7", 7), (30, 30), ("nonsense", 7), (0, 7), (-5, 7), (99999, 3650)]
)
def test_clamp_window(raw, expected):
    assert clamp_window(raw) == expected


# --- the queue-backed view ---------------------------------------------------


def test_queue_stats_view_reads_every_queue_it_is_given():
    done = MemoryTaskQueue("done")
    archived = MemoryTaskQueue("archived")
    done.put(issue_task("a", at=NOW))
    archived.put(issue_task("b", at=NOW, to=FAILED))

    view = QueueStatsView(queues=[done, archived], clock=FakeClock(NOW))
    report = view.report(7)
    assert report.overall.settled == 2
    assert report.window_days == 7


def test_queue_stats_view_memoises_within_the_ttl_and_refreshes_after():
    done = MemoryTaskQueue("done")
    done.put(issue_task("a", at=NOW))
    clock = FakeClock(NOW)
    view = QueueStatsView(queues=[done], clock=clock, ttl_seconds=5.0)

    assert view.report(7).overall.settled == 1
    done.put(issue_task("b", at=NOW))
    assert view.report(7).overall.settled == 1, "cached within the TTL"

    clock.instant = "2026-08-02T12:00:30Z"
    assert view.report(7).overall.settled == 2, "rescanned after the TTL"


def test_queue_stats_view_caches_per_window():
    done = MemoryTaskQueue("done")
    done.put(issue_task("a", at="2026-06-01T10:00:00Z"))
    view = QueueStatsView(queues=[done], clock=FakeClock(NOW))
    assert view.report(7).overall.settled == 0
    assert view.report(90).overall.settled == 1
