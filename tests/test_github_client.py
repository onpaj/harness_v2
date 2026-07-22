"""GithubClient — both the fake and the stdlib http driver (no network, via a fake opener)."""

import io
import json
import urllib.error

import pytest

from harness.drivers.github_client import (
    SELF_HEAL_LABEL,
    FakeGithubClient,
    HttpGithubClient,
    Issue,
    PullRequestInfo,
    PullRequestRef,
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

    client.remove_label("o/r", 1, "nope")  # must not crash

    assert client._issues[1].labels == ("harness:todo",)


def test_fake_add_existing_label_is_noop():
    client = FakeGithubClient([Issue(1, "A", "", "u1", ("harness:todo",))])

    client.add_label("o/r", 1, "harness:todo")

    assert client._issues[1].labels == ("harness:todo",)


# --- issues (create + marker search), fake ---------------------------------


def test_fake_create_issue_assigns_a_number_and_stores_it():
    client = FakeGithubClient([Issue(1, "A", "", "u1", ("harness:todo",))])

    created = client.create_issue(
        "o/r", title="Heal", body="marker <!-- x -->", labels=(SELF_HEAL_LABEL,)
    )

    assert created.number == 2  # next after the existing issue
    assert created.title == "Heal"
    assert client.list_issues("o/r", label=SELF_HEAL_LABEL)[0].number == 2


def test_fake_search_issue_by_marker_matches_body_within_the_label():
    client = FakeGithubClient()
    client.create_issue(
        "o/r", title="Heal", body="diagnosis\n<!-- harness-heal:tsk_9 -->\n",
        labels=(SELF_HEAL_LABEL,),
    )

    found = client.search_issue_by_marker("o/r", "<!-- harness-heal:tsk_9 -->")
    missing = client.search_issue_by_marker("o/r", "<!-- harness-heal:other -->")

    assert found is not None and found.title == "Heal"
    assert missing is None


def test_fake_search_ignores_issues_without_the_self_heal_label():
    client = FakeGithubClient(
        [Issue(1, "A", "<!-- harness-heal:tsk_9 -->", "u1", ("bug",))]
    )

    assert client.search_issue_by_marker("o/r", "<!-- harness-heal:tsk_9 -->") is None


# --- HttpGithubClient with a fake opener -----------------------------------


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
            "body": "details",
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
    assert req.get_header("Content-type") == "application/json"


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
    client.remove_label("o/r", 5, "gone")  # must not slip through


def test_http_remove_label_other_error_propagates():
    class ServerErrorOpener:
        def open(self, request):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=ServerErrorOpener())
    with pytest.raises(urllib.error.HTTPError):
        client.remove_label("o/r", 5, "x")


# --- pull requests, fake ---------------------------------------------------


def test_fake_find_pull_request_misses_then_hits():
    client = FakeGithubClient()

    assert client.find_pull_request("o/r", head="o:harness/tsk_1") is None

    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )

    assert client.find_pull_request("o/r", head="o:harness/tsk_1") == created


def test_fake_create_pull_request_records_the_call():
    client = FakeGithubClient()

    client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="trunk", title="T", body="B"
    )

    assert client.created == [
        {
            "repo": "o/r",
            "head": "o:harness/tsk_1",
            "base": "trunk",
            "title": "T",
            "body": "B",
        }
    ]


def test_fake_default_branch_is_configurable():
    assert FakeGithubClient().default_branch("o/r") == "main"
    assert FakeGithubClient(default_branch="trunk").default_branch("o/r") == "trunk"


# --- pull requests, http ---------------------------------------------------


def test_http_default_branch_reads_the_repo():
    opener = FakeOpener({"default_branch": "trunk"})
    client = HttpGithubClient("tok", opener=opener)

    assert client.default_branch("o/r") == "trunk"

    req = opener.requests[0]
    assert req.get_method() == "GET"
    assert req.full_url == "https://api.github.com/repos/o/r"


def test_http_find_pull_request_queries_by_head():
    payload = [{"number": 7, "html_url": "https://github.com/o/r/pull/7"}]
    opener = FakeOpener(payload)
    client = HttpGithubClient("tok", opener=opener)

    found = client.find_pull_request("o/r", head="o:harness/tsk_1")

    assert found.number == 7
    assert found.url == "https://github.com/o/r/pull/7"

    req = opener.requests[0]
    assert req.full_url.startswith("https://api.github.com/repos/o/r/pulls")
    assert "head=o%3Aharness%2Ftsk_1" in req.full_url
    assert "state=open" in req.full_url


def test_http_find_pull_request_returns_none_when_empty():
    client = HttpGithubClient("tok", opener=FakeOpener([]))

    assert client.find_pull_request("o/r", head="o:harness/tsk_1") is None


def test_http_find_pull_request_uses_head_label_from_response():
    # The response's head label differs from the argument on purpose: under the
    # old echo-the-argument behavior this would still (wrongly) pass with "head"
    # equal to the argument, so the assertion must pin the response's value.
    payload = [
        {
            "number": 7,
            "html_url": "https://github.com/o/r/pull/7",
            "head": {"label": "o:harness/actual-branch"},
        }
    ]
    client = HttpGithubClient("tok", opener=FakeOpener(payload))

    found = client.find_pull_request("o/r", head="o:harness/tsk_1")

    assert found.head == "o:harness/actual-branch"


def test_http_find_pull_request_falls_back_to_argument_when_head_missing():
    payload = [{"number": 7, "html_url": "https://github.com/o/r/pull/7"}]
    client = HttpGithubClient("tok", opener=FakeOpener(payload))

    found = client.find_pull_request("o/r", head="o:harness/tsk_1")

    assert found.head == "o:harness/tsk_1"


def test_http_create_pull_request_posts_the_payload():
    opener = FakeOpener({"number": 12, "html_url": "https://github.com/o/r/pull/12"})
    client = HttpGithubClient("tok", opener=opener)

    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )

    assert created.number == 12
    assert created.url == "https://github.com/o/r/pull/12"

    req = opener.requests[0]
    assert req.get_method() == "POST"
    assert req.full_url == "https://api.github.com/repos/o/r/pulls"
    assert json.loads(req.data.decode("utf-8")) == {
        "head": "o:harness/tsk_1",
        "base": "main",
        "title": "T",
        "body": "B",
    }
    assert req.get_header("Content-type") == "application/json"


def test_http_create_pull_request_uses_head_label_from_response():
    # Same deliberate mismatch as the find_pull_request case above: the response
    # label differs from the argument so the test fails under the old echo.
    payload = {
        "number": 12,
        "html_url": "https://github.com/o/r/pull/12",
        "head": {"label": "o:harness/actual-branch"},
    }
    client = HttpGithubClient("tok", opener=FakeOpener(payload))

    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )

    assert created.head == "o:harness/actual-branch"


def test_http_create_pull_request_falls_back_to_argument_when_head_missing():
    payload = {"number": 12, "html_url": "https://github.com/o/r/pull/12"}
    client = HttpGithubClient("tok", opener=FakeOpener(payload))

    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )

    assert created.head == "o:harness/tsk_1"


# --- get_pull_request, fake -------------------------------------------------


def test_fake_get_pull_request_reports_merged_state():
    client = FakeGithubClient()
    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )

    assert client.get_pull_request("o/r", created.number).merged is False

    client.merge_pull_request(created.number)

    assert client.get_pull_request("o/r", created.number).merged is True


def test_fake_get_pull_request_unknown_number_raises():
    client = FakeGithubClient()

    with pytest.raises(KeyError):
        client.get_pull_request("o/r", 999)


# --- get_pull_request, http --------------------------------------------------


def test_http_get_pull_request_reads_merged_field():
    payload = {"number": 7, "html_url": "https://github.com/o/r/pull/7", "merged": True}
    opener = FakeOpener(payload)
    client = HttpGithubClient("tok", opener=opener)

    detail = client.get_pull_request("o/r", 7)

    assert detail.number == 7
    assert detail.url == "https://github.com/o/r/pull/7"
    assert detail.merged is True

    req = opener.requests[0]
    assert req.get_method() == "GET"
    assert req.full_url == "https://api.github.com/repos/o/r/pulls/7"


def test_http_get_pull_request_open_pr_is_not_merged():
    payload = {"number": 7, "html_url": "https://github.com/o/r/pull/7", "merged": False}
    client = HttpGithubClient("tok", opener=FakeOpener(payload))

    assert client.get_pull_request("o/r", 7).merged is False


def test_http_get_pull_request_error_propagates():
    class ServerErrorOpener:
        def open(self, request):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=ServerErrorOpener())

    with pytest.raises(urllib.error.HTTPError):
        client.get_pull_request("o/r", 7)


# --- mergeability watcher support: PullRequestInfo, list_pull_requests, update_branch ---


def test_fake_list_pull_requests_filters_by_head_prefix():
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "harness/tsk_1", "sha1", "main", "behind")
    )
    client.add_pull_request(
        PullRequestInfo(2, "u2", "someone/manual", "sha2", "main", "dirty")
    )

    watched = client.list_pull_requests("o/r", head_prefix="harness/")

    assert [pr.number for pr in watched] == [1]


def test_fake_list_pull_requests_without_prefix_returns_all():
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "harness/tsk_1", "sha1", "main", "clean")
    )

    assert len(client.list_pull_requests("o/r")) == 1


def test_fake_update_branch_records_call_and_flips_to_clean():
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "harness/tsk_1", "sha1", "main", "behind")
    )

    client.update_branch("o/r", 1)

    assert client.updated_branches == [("o/r", 1)]
    [pr] = client.list_pull_requests("o/r")
    assert pr.mergeable_state == "clean"


def test_http_list_pull_requests_two_tier_fetch():
    list_payload = [
        {
            "number": 1,
            "html_url": "https://github.com/o/r/pull/1",
            "head": {"ref": "harness/tsk_1"},
            "base": {"ref": "main"},
        },
        {
            "number": 2,
            "html_url": "https://github.com/o/r/pull/2",
            "head": {"ref": "someone/manual"},
            "base": {"ref": "main"},
        },
    ]
    detail_payload = {
        "head": {"sha": "abc123"},
        "base": {"ref": "main"},
        "mergeable_state": "behind",
    }

    class TieredOpener:
        def __init__(self):
            self.requests = []

        def open(self, request):
            self.requests.append(request)
            if request.full_url.endswith("/pulls/1"):
                return FakeResponse(detail_payload)
            return FakeResponse(list_payload)

    opener = TieredOpener()
    client = HttpGithubClient("tok", opener=opener)

    infos = client.list_pull_requests("o/r", head_prefix="harness/")

    assert len(infos) == 1
    info = infos[0]
    assert info.number == 1
    assert info.head_branch == "harness/tsk_1"
    assert info.head_sha == "abc123"
    assert info.base_branch == "main"
    assert info.mergeable_state == "behind"
    # exactly one list call + one detail call (for the matching PR only)
    assert len(opener.requests) == 2


def test_http_list_pull_requests_missing_mergeable_state_is_unknown():
    class TieredOpener:
        def open(self, request):
            if "/pulls/1" in request.full_url and request.full_url.endswith("/pulls/1"):
                return FakeResponse({"head": {"sha": "x"}, "base": {"ref": "main"}})
            return FakeResponse(
                [
                    {
                        "number": 1,
                        "html_url": "u",
                        "head": {"ref": "harness/tsk_1"},
                        "base": {"ref": "main"},
                    }
                ]
            )

    client = HttpGithubClient("tok", opener=TieredOpener())

    [info] = client.list_pull_requests("o/r", head_prefix="harness/")

    assert info.mergeable_state == "unknown"


def test_http_update_branch_puts_update_branch_endpoint():
    opener = FakeOpener({})
    client = HttpGithubClient("tok", opener=opener)

    client.update_branch("o/r", 5)

    req = opener.requests[0]
    assert req.get_method() == "PUT"
    assert req.full_url == "https://api.github.com/repos/o/r/pulls/5/update-branch"


def test_http_update_branch_422_is_swallowed():
    class NotBehindOpener:
        def open(self, request):
            raise urllib.error.HTTPError(
                request.full_url, 422, "Unprocessable", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=NotBehindOpener())
    client.update_branch("o/r", 5)  # must not raise


def test_http_update_branch_other_error_propagates():
    class ServerErrorOpener:
        def open(self, request):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=ServerErrorOpener())
    with pytest.raises(urllib.error.HTTPError):
        client.update_branch("o/r", 5)


# --- issues, http ----------------------------------------------------------


def test_http_create_issue_posts_title_body_labels():
    opener = FakeOpener(
        {
            "number": 42,
            "title": "Heal",
            "body": "B",
            "html_url": "https://github.com/o/r/issues/42",
            "labels": [{"name": "harness:self-heal"}],
        }
    )
    client = HttpGithubClient("tok", opener=opener)

    created = client.create_issue(
        "o/r", title="Heal", body="B", labels=("harness:self-heal",)
    )

    assert created.number == 42
    assert created.url == "https://github.com/o/r/issues/42"
    assert created.labels == ("harness:self-heal",)

    req = opener.requests[0]
    assert req.get_method() == "POST"
    assert req.full_url == "https://api.github.com/repos/o/r/issues"
    assert json.loads(req.data.decode("utf-8")) == {
        "title": "Heal",
        "body": "B",
        "labels": ["harness:self-heal"],
    }
    assert req.get_header("Content-type") == "application/json"


def test_http_search_issue_by_marker_scans_self_heal_issues():
    payload = [
        {
            "number": 7,
            "title": "Heal",
            "body": "diagnosis <!-- harness-heal:tsk_9 --> end",
            "html_url": "https://github.com/o/r/issues/7",
            "labels": [{"name": "harness:self-heal"}],
        }
    ]
    opener = FakeOpener(payload)
    client = HttpGithubClient("tok", opener=opener)

    found = client.search_issue_by_marker("o/r", "<!-- harness-heal:tsk_9 -->")
    assert found is not None and found.number == 7

    # scoped the listing to the self-heal label
    assert "labels=harness%3Aself-heal" in opener.requests[0].full_url


def test_http_search_issue_by_marker_returns_none_when_no_body_matches():
    payload = [
        {
            "number": 7,
            "title": "Heal",
            "body": "unrelated",
            "html_url": "https://github.com/o/r/issues/7",
            "labels": [{"name": "harness:self-heal"}],
        }
    ]
    client = HttpGithubClient("tok", opener=FakeOpener(payload))

    assert client.search_issue_by_marker("o/r", "<!-- harness-heal:tsk_9 -->") is None
