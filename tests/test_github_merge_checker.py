"""GithubMergeChecker — driven by FakeGithubClient (no network)."""

import pytest

from harness.drivers.github_client import FakeGithubClient
from harness.drivers.github_merge_checker import GithubMergeChecker
from harness.models import Task


def make_task(pr: dict | None = None) -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-21T10:00:00Z",
        status="end",
        data={"pr": pr} if pr is not None else {},
    )


def test_returns_none_when_task_has_no_pr():
    checker = GithubMergeChecker(FakeGithubClient())

    assert checker.is_merged(make_task()) is None


def test_returns_none_when_pr_data_is_malformed():
    checker = GithubMergeChecker(FakeGithubClient())

    assert checker.is_merged(make_task({"repo": "o/r"})) is None
    assert checker.is_merged(make_task({"number": "not-an-int", "repo": "o/r"})) is None


def test_returns_false_for_an_open_pr():
    client = FakeGithubClient()
    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )
    checker = GithubMergeChecker(client)
    task = make_task({"repo": "o/r", "number": created.number})

    assert checker.is_merged(task) is False


def test_returns_true_once_merged():
    client = FakeGithubClient()
    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )
    client.merge_pull_request(created.number)
    checker = GithubMergeChecker(client)
    task = make_task({"repo": "o/r", "number": created.number})

    assert checker.is_merged(task) is True


def test_transient_failure_raises():
    class BrokenClient(FakeGithubClient):
        def get_pull_request(self, repo, number):
            raise RuntimeError("network blip")

    checker = GithubMergeChecker(BrokenClient())
    task = make_task({"repo": "o/r", "number": 1})

    with pytest.raises(RuntimeError, match="network blip"):
        checker.is_merged(task)
