"""GithubClient — fake i stdlib http driver (bez sítě, přes fake opener)."""

import io
import json
import urllib.error

import pytest

from harness.drivers.github_client import (
    FakeGithubClient,
    HttpGithubClient,
    Issue,
)


# --- FakeGithubClient ------------------------------------------------------


def test_fake_list_issues_filters_by_label():
    client = FakeGithubClient(
        [
            Issue(1, "A", "", "u1", ("harness:todo",)),
            Issue(2, "B", "", "u2", ("other",)),
        ]
    )

    todo = client.list_issues("o/r", label="harness:todo")

    assert [i.number for i in todo] == [1]


def test_fake_add_and_remove_label_mutate():
    client = FakeGithubClient([Issue(1, "A", "", "u1", ("harness:todo",))])

    client.add_label("o/r", 1, "harness:queued")
    client.remove_label("o/r", 1, "harness:todo")

    labels = client.list_issues("o/r", label="harness:queued")[0].labels
    assert set(labels) == {"harness:queued"}


def test_fake_remove_absent_label_is_noop():
    client = FakeGithubClient([Issue(1, "A", "", "u1", ("harness:todo",))])

    client.remove_label("o/r", 1, "nope")  # nesmí spadnout

    assert client._issues[1].labels == ("harness:todo",)


def test_fake_add_existing_label_is_noop():
    client = FakeGithubClient([Issue(1, "A", "", "u1", ("harness:todo",))])

    client.add_label("o/r", 1, "harness:todo")

    assert client._issues[1].labels == ("harness:todo",)


# --- HttpGithubClient s fake openerem --------------------------------------


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
        self.payload = payload if payload is not None else []
        self.requests = []

    def open(self, request):
        self.requests.append(request)
        return FakeResponse(self.payload)


def test_http_list_issues_maps_fields_and_filters_out_prs():
    payload = [
        {
            "number": 1,
            "title": "Bug",
            "body": "detaily",
            "html_url": "https://github.com/o/r/issues/1",
            "labels": [{"name": "harness:todo"}, {"name": "bug"}],
        },
        {
            "number": 2,
            "title": "PR",
            "body": "",
            "html_url": "https://github.com/o/r/pull/2",
            "labels": [],
            "pull_request": {"url": "..."},
        },
    ]
    opener = FakeOpener(payload)
    client = HttpGithubClient("tok", opener=opener)

    issues = client.list_issues("o/r", label="harness:todo")

    assert len(issues) == 1
    issue = issues[0]
    assert issue.number == 1
    assert issue.title == "Bug"
    assert issue.url == "https://github.com/o/r/issues/1"
    assert issue.labels == ("harness:todo", "bug")

    req = opener.requests[0]
    assert req.get_method() == "GET"
    assert req.full_url.startswith("https://api.github.com/repos/o/r/issues")
    assert "labels=harness%3Atodo" in req.full_url
    assert "state=open" in req.full_url
    assert req.get_header("Authorization") == "Bearer tok"


def test_http_add_label_posts_labels_body():
    opener = FakeOpener({})
    client = HttpGithubClient("tok", opener=opener)

    client.add_label("o/r", 5, "harness:queued")

    req = opener.requests[0]
    assert req.get_method() == "POST"
    assert req.full_url == "https://api.github.com/repos/o/r/issues/5/labels"
    assert json.loads(req.data.decode("utf-8")) == {"labels": ["harness:queued"]}


def test_http_remove_label_deletes_and_swallows_404():
    opener = FakeOpener({})
    client = HttpGithubClient("tok", opener=opener)

    client.remove_label("o/r", 5, "harness:todo")

    req = opener.requests[0]
    assert req.get_method() == "DELETE"
    assert req.full_url == "https://api.github.com/repos/o/r/issues/5/labels/harness%3Atodo"


def test_http_remove_label_404_is_swallowed():
    class NotFoundOpener:
        def open(self, request):
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=NotFoundOpener())
    client.remove_label("o/r", 5, "gone")  # nesmí propadnout


def test_http_remove_label_other_error_propagates():
    class ServerErrorOpener:
        def open(self, request):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=ServerErrorOpener())
    with pytest.raises(urllib.error.HTTPError):
        client.remove_label("o/r", 5, "x")
