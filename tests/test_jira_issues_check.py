"""JiraIssuesCheck — the inbound Jira label scan as a Check (no network)."""

from __future__ import annotations

import pytest

from harness.drivers.jira_client import FakeJiraClient, JiraIssue
from harness.drivers.jira_issues_check import JiraIssuesCheck


def test_emits_one_observation_per_labelled_issue_with_provenance():
    client = FakeJiraClient(
        [JiraIssue("PROJ-7", "Fix bug", "the body", "https://gh/browse/PROJ-7", ("harness-todo",), "PROJ")],
        site="https://acme.atlassian.net",
    )
    check = JiraIssuesCheck(client=client, repository="my-service", project="PROJ")

    obs = check.evaluate()

    assert len(obs) == 1
    (o,) = obs
    assert o.state_key == "jira:PROJ-7"
    assert o.repository == "my-service"
    assert o.data["title"] == "Fix bug"
    assert o.data["body"] == "the body"
    assert o.data["source"] == {
        "kind": "jira",
        "site": "https://acme.atlassian.net",
        "key": "PROJ-7",
        "url": "https://gh/browse/PROJ-7",
        "project": "PROJ",
    }


def test_claims_by_swapping_the_label():
    client = FakeJiraClient(
        [JiraIssue("PROJ-7", "t", "b", "u", ("harness-todo", "bug"), "PROJ")]
    )
    check = JiraIssuesCheck(client=client, repository="my-service", project="PROJ")

    check.evaluate()

    remaining = client.search_issues('labels = "harness-queued"')
    assert [i.key for i in remaining] == ["PROJ-7"]
    assert client.search_issues('labels = "harness-todo"') == []
    (issue,) = client.search_issues('labels = "bug"')
    assert set(issue.labels) == {"harness-queued", "bug"}


def test_claimed_ledger_suppresses_a_relisted_issue_within_the_process():
    # An issue that (because of read-after-write lag) still lists under the
    # select label on a second evaluate() must not produce a second task.
    class LaggyClient(FakeJiraClient):
        def remove_label(self, key, label):  # no-op: simulate lag
            return None

    client = LaggyClient([JiraIssue("PROJ-7", "t", "b", "u", ("harness-todo",), "PROJ")])
    check = JiraIssuesCheck(client=client, repository="my-service", project="PROJ")

    first = check.evaluate()
    second = check.evaluate()

    assert len(first) == 1
    assert second == []


def test_no_labelled_issues_yields_no_observations():
    client = FakeJiraClient([JiraIssue("PROJ-1", "t", "b", "u", ("bug",), "PROJ")])
    check = JiraIssuesCheck(client=client, repository="my-service", project="PROJ")

    assert check.evaluate() == []


def test_default_label_and_claimed_label():
    client = FakeJiraClient(
        [JiraIssue("PROJ-1", "t", "b", "u", ("harness-todo",), "PROJ")]
    )
    check = JiraIssuesCheck(client=client, repository="my-service", project="PROJ")

    check.evaluate()

    assert client.search_issues('labels = "harness-queued"')[0].key == "PROJ-1"


def test_custom_label_and_claimed_label():
    client = FakeJiraClient(
        [JiraIssue("PROJ-1", "t", "b", "u", ("todo",), "PROJ")]
    )
    check = JiraIssuesCheck(
        client=client, repository="my-service", project="PROJ",
        label="todo", claimed_label="doing",
    )

    check.evaluate()

    assert client.search_issues('labels = "doing"')[0].key == "PROJ-1"
    assert client.search_issues('labels = "todo"') == []


def test_explicit_jql_overrides_project_label_form():
    client = FakeJiraClient(
        [JiraIssue("PROJ-1", "t", "b", "u", ("harness-todo",), "PROJ")]
    )
    check = JiraIssuesCheck(
        client=client, repository="my-service", jql='labels = "harness-todo"'
    )

    obs = check.evaluate()

    assert len(obs) == 1


def test_requires_jql_or_project():
    client = FakeJiraClient()

    with pytest.raises(ValueError):
        JiraIssuesCheck(client=client, repository="my-service")
