"""The IssueTracker port contract."""

import pytest

from harness.ports.issues import IssueError, IssueRef, IssueTracker


def test_issue_tracker_is_abstract():
    with pytest.raises(TypeError):
        IssueTracker()  # type: ignore[abstract]


def test_a_trivial_subclass_satisfies_the_signature():
    class Stub(IssueTracker):
        def open_issue(self, repo, *, title, body, labels, marker):
            return IssueRef(number=1, url=f"https://x/{repo}/1")

    ref = Stub().open_issue(
        "o/r", title="T", body="B", labels=("harness:self-heal",), marker="tsk_1"
    )
    assert ref == IssueRef(number=1, url="https://x/o/r/1")


def test_issue_error_is_an_exception():
    assert issubclass(IssueError, Exception)
