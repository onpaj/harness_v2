"""MemoryIssueTracker — the in-memory fake, idempotent by marker."""

from harness.drivers.memory import MemoryIssueTracker


def test_open_once_records_one_issue():
    tracker = MemoryIssueTracker()

    ref = tracker.open_issue(
        "o/r",
        title="T",
        body="B",
        labels=("harness:self-heal",),
        marker="tsk_1",
        scope_label="harness:self-heal",
    )

    assert ref.number == 1
    assert len(tracker.opened) == 1
    assert tracker.opened[0]["title"] == "T"


def test_same_marker_returns_the_existing_issue():
    tracker = MemoryIssueTracker()

    first = tracker.open_issue(
        "o/r", title="T", body="B", labels=(), marker="tsk_1", scope_label="harness:self-heal"
    )
    again = tracker.open_issue(
        "o/r",
        title="different",
        body="different",
        labels=(),
        marker="tsk_1",
        scope_label="harness:self-heal",
    )

    assert again == first
    assert len(tracker.opened) == 1  # not filed twice


def test_different_marker_files_a_second_issue():
    tracker = MemoryIssueTracker()

    tracker.open_issue(
        "o/r", title="T", body="B", labels=(), marker="tsk_1", scope_label="harness:self-heal"
    )
    second = tracker.open_issue(
        "o/r", title="T2", body="B2", labels=(), marker="tsk_2", scope_label="harness:self-heal"
    )

    assert second.number == 2
    assert len(tracker.opened) == 2


def test_same_marker_different_repo_is_a_separate_issue():
    tracker = MemoryIssueTracker()

    tracker.open_issue(
        "o/r", title="T", body="B", labels=(), marker="tsk_1", scope_label="harness:self-heal"
    )
    other = tracker.open_issue(
        "o/other", title="T", body="B", labels=(), marker="tsk_1", scope_label="harness:self-heal"
    )

    assert other.number == 2
    assert len(tracker.opened) == 2


def test_scope_label_is_carried_onto_the_opened_issue():
    tracker = MemoryIssueTracker()

    tracker.open_issue(
        "onpaj/harness_v2",
        title="A finding",
        body="body",
        labels=("tech-debt",),
        marker="tsk_1:abcd1234",
        scope_label="arch-review",
    )

    assert tracker.opened[0]["labels"] == ("tech-debt", "arch-review")
    assert tracker.opened[0]["scope_label"] == "arch-review"


def test_the_same_marker_under_a_different_scope_label_is_a_different_issue():
    tracker = MemoryIssueTracker()
    for scope in ("arch-review", "harness:self-heal"):
        tracker.open_issue(
            "onpaj/harness_v2",
            title="A finding",
            body="body",
            labels=(),
            marker="tsk_1:abcd1234",
            scope_label=scope,
        )

    assert len(tracker.opened) == 2
