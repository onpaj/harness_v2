"""MemoryIssueTracker — the in-memory fake, idempotent by marker."""

from harness.drivers.memory import MemoryIssueTracker


def test_open_once_records_one_issue():
    tracker = MemoryIssueTracker()

    ref = tracker.open_issue(
        "o/r", title="T", body="B", labels=("harness:self-heal",), marker="tsk_1"
    )

    assert ref.number == 1
    assert len(tracker.opened) == 1
    assert tracker.opened[0]["title"] == "T"


def test_same_marker_returns_the_existing_issue():
    tracker = MemoryIssueTracker()

    first = tracker.open_issue(
        "o/r", title="T", body="B", labels=(), marker="tsk_1"
    )
    again = tracker.open_issue(
        "o/r", title="different", body="different", labels=(), marker="tsk_1"
    )

    assert again == first
    assert len(tracker.opened) == 1  # not filed twice


def test_different_marker_files_a_second_issue():
    tracker = MemoryIssueTracker()

    tracker.open_issue("o/r", title="T", body="B", labels=(), marker="tsk_1")
    second = tracker.open_issue("o/r", title="T2", body="B2", labels=(), marker="tsk_2")

    assert second.number == 2
    assert len(tracker.opened) == 2


def test_same_marker_different_repo_is_a_separate_issue():
    tracker = MemoryIssueTracker()

    tracker.open_issue("o/r", title="T", body="B", labels=(), marker="tsk_1")
    other = tracker.open_issue("o/other", title="T", body="B", labels=(), marker="tsk_1")

    assert other.number == 2
    assert len(tracker.opened) == 2
