"""GithubIssueTracker — idempotent issue creation over GithubClient."""

import pytest

from harness.drivers.github_client import SELF_HEAL_LABEL, FakeGithubClient, Issue
from harness.drivers.github_issues import GithubIssueTracker, marker_comment
from harness.ports.issues import IssueError


def test_open_issue_creates_with_the_marker_embedded():
    client = FakeGithubClient()
    tracker = GithubIssueTracker(client)

    ref = tracker.open_issue(
        "o/r", title="Heal it", body="diagnosis", labels=("harness:self-heal",),
        marker="tsk_9",
    )

    created = client.list_issues("o/r", label=SELF_HEAL_LABEL)
    assert len(created) == 1
    issue = created[0]
    assert issue.number == ref.number
    assert marker_comment("tsk_9") in issue.body  # dedup marker present
    assert issue.title == "Heal it"


def test_open_issue_is_idempotent_by_marker():
    client = FakeGithubClient()
    tracker = GithubIssueTracker(client)

    first = tracker.open_issue(
        "o/r", title="Heal", body="d", labels=(), marker="tsk_9"
    )
    again = tracker.open_issue(
        "o/r", title="different", body="different", labels=(), marker="tsk_9"
    )

    assert again == first
    assert len(client.list_issues("o/r", label=SELF_HEAL_LABEL)) == 1  # no second issue


def test_open_issue_always_carries_the_self_heal_label():
    client = FakeGithubClient()
    tracker = GithubIssueTracker(client)

    tracker.open_issue("o/r", title="Heal", body="d", labels=("custom",), marker="tsk_9")

    issue = client.list_issues("o/r", label=SELF_HEAL_LABEL)[0]
    assert "custom" in issue.labels and SELF_HEAL_LABEL in issue.labels


def test_client_error_becomes_issue_error():
    class BoomClient(FakeGithubClient):
        def search_issue_by_marker(self, repo, marker):
            return None

        def create_issue(self, repo, *, title, body, labels):
            import io
            import urllib.error

            raise urllib.error.HTTPError(
                "https://api.github.com", 422, "Unprocessable", {}, io.BytesIO(b"")
            )

    tracker = GithubIssueTracker(BoomClient())

    with pytest.raises(IssueError):
        tracker.open_issue("o/r", title="T", body="B", labels=(), marker="tsk_9")


def test_existing_issue_is_returned_without_creating():
    client = FakeGithubClient(
        [
            Issue(
                5,
                "Old",
                f"body {marker_comment('tsk_9')}",
                "https://github.com/o/r/issues/5",
                (SELF_HEAL_LABEL,),
            )
        ]
    )
    tracker = GithubIssueTracker(client)

    ref = tracker.open_issue("o/r", title="new", body="new", labels=(), marker="tsk_9")

    assert ref.number == 5
    assert ref.url == "https://github.com/o/r/issues/5"
