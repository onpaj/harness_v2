"""JiraClient — both the fake and the stdlib http driver (no network, via a fake opener)."""

import io
import json
import urllib.error

import pytest

from harness.drivers.jira_client import FakeJiraClient, HttpJiraClient, JiraIssue


# --- FakeJiraClient ----------------------------------------------------------


def test_fake_search_issues_filters_by_label_predicate():
    client = FakeJiraClient(
        [
            JiraIssue("PROJ-1", "A", "", "u1", ("harness-todo",), "PROJ"),
            JiraIssue("PROJ-2", "B", "", "u2", ("other",), "PROJ"),
        ]
    )

    todo = client.search_issues('project = PROJ AND labels = "harness-todo"')

    assert [i.key for i in todo] == ["PROJ-1"]


def test_fake_search_issues_with_no_label_predicate_returns_everything():
    client = FakeJiraClient(
        [JiraIssue("PROJ-1", "A", "", "u1", ("harness-todo",), "PROJ")]
    )

    assert [i.key for i in client.search_issues("project = PROJ")] == ["PROJ-1"]


def test_fake_add_and_remove_label_mutate():
    client = FakeJiraClient([JiraIssue("PROJ-1", "A", "", "u1", ("harness-todo",), "PROJ")])

    client.add_label("PROJ-1", "harness-queued")
    client.remove_label("PROJ-1", "harness-todo")

    labels = client.search_issues("")[0].labels
    assert set(labels) == {"harness-queued"}


def test_fake_remove_absent_label_is_noop():
    client = FakeJiraClient([JiraIssue("PROJ-1", "A", "", "u1", ("harness-todo",), "PROJ")])

    client.remove_label("PROJ-1", "nope")  # must not crash

    assert client._issues["PROJ-1"].labels == ("harness-todo",)


def test_fake_add_existing_label_is_noop():
    client = FakeJiraClient([JiraIssue("PROJ-1", "A", "", "u1", ("harness-todo",), "PROJ")])

    client.add_label("PROJ-1", "harness-todo")

    assert client._issues["PROJ-1"].labels == ("harness-todo",)


def test_fake_site_is_configurable():
    assert FakeJiraClient().site == "https://fake.atlassian.net"
    assert FakeJiraClient(site="https://acme.atlassian.net").site == "https://acme.atlassian.net"


# --- HttpJiraClient with a fake opener ---------------------------------------


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else {"issues": []}
        self.requests = []

    def open(self, request):
        self.requests.append(request)
        return FakeResponse(self.payload)


def test_http_search_issues_maps_fields_and_builds_browse_url():
    payload = {
        "issues": [
            {
                "key": "PROJ-7",
                "fields": {
                    "summary": "Fix bug",
                    "description": "plain text",
                    "labels": ["harness-todo", "bug"],
                    "project": {"key": "PROJ"},
                },
            }
        ]
    }
    opener = FakeOpener(payload)
    client = HttpJiraClient("https://acme.atlassian.net", "e@x.com", "tok", opener=opener)

    issues = client.search_issues('project = PROJ AND labels = "harness-todo"')

    assert len(issues) == 1
    issue = issues[0]
    assert issue.key == "PROJ-7"
    assert issue.summary == "Fix bug"
    assert issue.description == "plain text"
    assert issue.url == "https://acme.atlassian.net/browse/PROJ-7"
    assert issue.labels == ("harness-todo", "bug")
    assert issue.project == "PROJ"

    req = opener.requests[0]
    assert req.get_method() == "GET"
    assert req.full_url.startswith("https://acme.atlassian.net/rest/api/3/search")
    assert "jql=" in req.full_url
    assert req.get_header("Authorization").startswith("Basic ")


def test_http_search_issues_extracts_plain_text_from_adf_description():
    payload = {
        "issues": [
            {
                "key": "PROJ-1",
                "fields": {
                    "summary": "S",
                    "description": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "Hello"}],
                            },
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": "World"}],
                            },
                        ],
                    },
                    "labels": [],
                    "project": {"key": "PROJ"},
                },
            }
        ]
    }
    client = HttpJiraClient("https://acme.atlassian.net", "e@x.com", "tok", opener=FakeOpener(payload))

    [issue] = client.search_issues("project = PROJ")

    assert issue.description == "Hello\nWorld"


def test_http_search_issues_missing_description_is_empty_string():
    payload = {
        "issues": [
            {
                "key": "PROJ-1",
                "fields": {"summary": "S", "labels": [], "project": {"key": "PROJ"}},
            }
        ]
    }
    client = HttpJiraClient("https://acme.atlassian.net", "e@x.com", "tok", opener=FakeOpener(payload))

    [issue] = client.search_issues("project = PROJ")

    assert issue.description == ""


def test_http_site_strips_trailing_slash():
    client = HttpJiraClient("https://acme.atlassian.net/", "e@x.com", "tok")
    assert client.site == "https://acme.atlassian.net"


def test_http_add_label_puts_update_labels_body():
    opener = FakeOpener({})
    client = HttpJiraClient("https://acme.atlassian.net", "e@x.com", "tok", opener=opener)

    client.add_label("PROJ-5", "harness-queued")

    req = opener.requests[0]
    assert req.get_method() == "PUT"
    assert req.full_url == "https://acme.atlassian.net/rest/api/3/issue/PROJ-5"
    assert json.loads(req.data.decode("utf-8")) == {
        "update": {"labels": [{"add": "harness-queued"}]}
    }
    assert req.get_header("Content-type") == "application/json"


def test_http_remove_label_puts_remove_op():
    opener = FakeOpener({})
    client = HttpJiraClient("https://acme.atlassian.net", "e@x.com", "tok", opener=opener)

    client.remove_label("PROJ-5", "harness-todo")

    req = opener.requests[0]
    assert req.get_method() == "PUT"
    assert json.loads(req.data.decode("utf-8")) == {
        "update": {"labels": [{"remove": "harness-todo"}]}
    }


def test_http_remove_label_404_is_swallowed():
    class NotFoundOpener:
        def open(self, request):
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b"")
            )

    client = HttpJiraClient("https://acme.atlassian.net", "e@x.com", "tok", opener=NotFoundOpener())
    client.remove_label("PROJ-5", "gone")  # must not raise


def test_http_remove_label_other_error_propagates():
    class ServerErrorOpener:
        def open(self, request):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b"")
            )

    client = HttpJiraClient("https://acme.atlassian.net", "e@x.com", "tok", opener=ServerErrorOpener())
    with pytest.raises(urllib.error.HTTPError):
        client.remove_label("PROJ-5", "x")


def test_http_add_label_error_propagates_unwrapped():
    class ServerErrorOpener:
        def open(self, request):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b"")
            )

    client = HttpJiraClient("https://acme.atlassian.net", "e@x.com", "tok", opener=ServerErrorOpener())
    with pytest.raises(urllib.error.HTTPError):
        client.add_label("PROJ-5", "x")
