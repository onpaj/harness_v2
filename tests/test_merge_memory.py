import pytest

from harness.drivers.memory import FakeMergeChecker
from harness.models import Task


def make_task(pr: dict | None = None) -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-21T10:00:00Z",
        status="end",
        data={"pr": pr} if pr is not None else {},
    )


def test_no_pr_returns_none():
    checker = FakeMergeChecker()

    assert checker.is_merged(make_task()) is None


def test_unmarked_pr_is_not_merged():
    checker = FakeMergeChecker()

    assert checker.is_merged(make_task({"repo": "o/r", "number": 1})) is False


def test_marked_pr_is_merged():
    checker = FakeMergeChecker()
    checker.merged.add(("o/r", 1))

    assert checker.is_merged(make_task({"repo": "o/r", "number": 1})) is True


def test_marked_raises_key_raises():
    checker = FakeMergeChecker()
    checker.raises.add(("o/r", 1))

    with pytest.raises(RuntimeError):
        checker.is_merged(make_task({"repo": "o/r", "number": 1}))
