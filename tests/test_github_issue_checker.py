"""GithubIssueChecker — driven by FakeGithubClient (no network)."""

import pytest

from harness.drivers.github_client import FakeGithubClient, Issue
from harness.drivers.github_issue_checker import GithubIssueChecker
from harness.models import Task


def _task(source: dict | None) -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-22T10:00:00Z",
        status="plan",
        data={"source": source} if source is not None else {},
    )


def _client_with_issue(number=1, *, labels=("harness:todo",)) -> FakeGithubClient:
    return FakeGithubClient([Issue(number, "Bug", "", f"u{number}", labels)])


def test_returns_none_when_task_has_no_source():
    checker = GithubIssueChecker(FakeGithubClient())

    assert checker.is_open(_task(None)) is None


def test_returns_none_for_a_foreign_source_kind():
    checker = GithubIssueChecker(FakeGithubClient())

    assert checker.is_open(_task({"kind": "memory", "issue": "issue-0"})) is None


def test_returns_none_when_source_is_malformed():
    checker = GithubIssueChecker(_client_with_issue())

    assert checker.is_open(_task({"kind": "github", "repo": "o/r"})) is None
    assert checker.is_open(
        _task({"kind": "github", "repo": "o/r", "issue": "not-an-int"})
    ) is None


def test_returns_true_for_an_open_issue():
    checker = GithubIssueChecker(_client_with_issue(number=7))
    task = _task({"kind": "github", "repo": "o/r", "issue": 7})

    assert checker.is_open(task) is True


def test_returns_false_for_a_closed_issue():
    client = _client_with_issue(number=7)
    client.close_issue(7)
    checker = GithubIssueChecker(client)
    task = _task({"kind": "github", "repo": "o/r", "issue": 7})

    assert checker.is_open(task) is False


def test_returns_false_for_a_deleted_issue():
    client = _client_with_issue(number=7)
    client.close_issue(7, deleted=True)
    checker = GithubIssueChecker(client)
    task = _task({"kind": "github", "repo": "o/r", "issue": 7})

    # The issue is gone (get_issue_state → None), which is "not open".
    assert checker.is_open(task) is False


def test_transient_failure_raises():
    class BrokenClient(FakeGithubClient):
        def get_issue_state(self, repo, number):
            raise RuntimeError("network blip")

    checker = GithubIssueChecker(BrokenClient())
    task = _task({"kind": "github", "repo": "o/r", "issue": 1})

    with pytest.raises(RuntimeError, match="network blip"):
        checker.is_open(task)
