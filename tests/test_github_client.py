"""GithubClient — both the fake and the stdlib http driver (no network, via a fake opener)."""

import io
import json
import urllib.error

import pytest

from harness.drivers.github_client import (
    SELF_HEAL_LABEL,
    CheckRun,
    FakeGithubClient,
    HttpGithubClient,
    Issue,
    PullRequestInfo,
    PullRequestRef,
    _StripCrossHostAuth,
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

    found = client.search_issue_by_marker(
        "o/r", "<!-- harness-heal:tsk_9 -->", label=SELF_HEAL_LABEL
    )
    missing = client.search_issue_by_marker(
        "o/r", "<!-- harness-heal:other -->", label=SELF_HEAL_LABEL
    )

    assert found is not None and found.title == "Heal"
    assert missing is None


def test_fake_search_ignores_issues_without_the_self_heal_label():
    client = FakeGithubClient(
        [Issue(1, "A", "<!-- harness-heal:tsk_9 -->", "u1", ("bug",))]
    )

    assert (
        client.search_issue_by_marker(
            "o/r", "<!-- harness-heal:tsk_9 -->", label=SELF_HEAL_LABEL
        )
        is None
    )


# --- issue state (open/closed/gone), fake ----------------------------------


def test_fake_get_issue_state_open_by_default():
    client = FakeGithubClient([Issue(1, "A", "", "u1", ("harness:todo",))])

    assert client.get_issue_state("o/r", 1) == "open"


def test_fake_get_issue_state_closed_after_close():
    client = FakeGithubClient([Issue(1, "A", "", "u1", ("harness:todo",))])

    client.close_issue(1)

    assert client.get_issue_state("o/r", 1) == "closed"
    # A closed issue also drops out of the label listing, mirroring state=open.
    assert client.list_issues("o/r", label="harness:todo") == []


def test_fake_get_issue_state_none_when_missing():
    client = FakeGithubClient()

    assert client.get_issue_state("o/r", 999) is None


def test_fake_close_issue_deleted_removes_it():
    client = FakeGithubClient([Issue(1, "A", "", "u1", ("harness:todo",))])

    client.close_issue(1, deleted=True)

    assert client.get_issue_state("o/r", 1) is None


# --- get_issue (point lookup by number, no label required), fake -----------


def test_fake_get_issue_returns_issue_with_no_label_at_all():
    client = FakeGithubClient([Issue(1, "A", "body", "u1", ())])

    found = client.get_issue("o/r", 1)

    assert found is not None
    assert found.number == 1
    assert found.title == "A"
    assert found.labels == ()


def test_fake_get_issue_returns_closed_issue_too():
    client = FakeGithubClient([Issue(1, "A", "", "u1", ())])
    client.close_issue(1)

    found = client.get_issue("o/r", 1)

    assert found is not None and found.number == 1


def test_fake_get_issue_none_when_missing():
    client = FakeGithubClient()

    assert client.get_issue("o/r", 999) is None


# --- get_issue, http ---------------------------------------------------------


def test_http_get_issue_maps_fields():
    payload = {
        "number": 9,
        "title": "No label",
        "body": "details",
        "html_url": "https://github.com/o/r/issues/9",
        "labels": [],
    }
    opener = FakeOpener(payload)
    client = HttpGithubClient("tok", opener=opener)

    found = client.get_issue("o/r", 9)

    assert found is not None
    assert found.number == 9
    assert found.title == "No label"
    assert found.body == "details"
    assert found.url == "https://github.com/o/r/issues/9"
    assert found.labels == ()

    req = opener.requests[0]
    assert req.get_method() == "GET"
    assert req.full_url == "https://api.github.com/repos/o/r/issues/9"


def test_http_get_issue_404_is_none():
    class NotFoundOpener:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=NotFoundOpener())

    assert client.get_issue("o/r", 9) is None


def test_http_get_issue_other_error_propagates():
    class ServerErrorOpener:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=ServerErrorOpener())

    with pytest.raises(urllib.error.HTTPError):
        client.get_issue("o/r", 9)


# --- HttpGithubClient with a fake opener -----------------------------------


class FakeResponse:
    def __init__(self, payload, headers=None):
        self._body = json.dumps(payload).encode("utf-8")
        # A real `urllib` response always carries headers; `list_pull_requests`
        # reads `Link` off them to follow pagination.
        self.headers = headers if headers is not None else {}

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
        self.timeouts = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
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
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=NotFoundOpener())
    client.remove_label("o/r", 5, "gone")  # must not slip through


def test_http_remove_label_other_error_propagates():
    class ServerErrorOpener:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=ServerErrorOpener())
    with pytest.raises(urllib.error.HTTPError):
        client.remove_label("o/r", 5, "x")


# --- issue state (open/closed/gone), http ----------------------------------


def test_http_get_issue_state_reads_the_state_field():
    opener = FakeOpener({"number": 7, "state": "closed"})
    client = HttpGithubClient("tok", opener=opener)

    assert client.get_issue_state("o/r", 7) == "closed"

    req = opener.requests[0]
    assert req.get_method() == "GET"
    assert req.full_url == "https://api.github.com/repos/o/r/issues/7"


def test_http_get_issue_state_404_is_none():
    class NotFoundOpener:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=NotFoundOpener())

    assert client.get_issue_state("o/r", 7) is None


def test_http_get_issue_state_other_error_propagates():
    class ServerErrorOpener:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=ServerErrorOpener())

    with pytest.raises(urllib.error.HTTPError):
        client.get_issue_state("o/r", 7)


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


# --- get_pull_request, fake --------------------------------------------------


def test_fake_get_pull_request_defaults_to_open():
    client = FakeGithubClient()
    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )

    detail = client.get_pull_request("o/r", number=created.number)

    assert detail.number == created.number
    assert detail.url == created.url
    assert detail.state == "open"
    assert detail.merged is False


def test_fake_close_pull_request_marks_merged():
    client = FakeGithubClient()
    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )

    client.close_pull_request(created.number, merged=True)

    detail = client.get_pull_request("o/r", number=created.number)
    assert detail.state == "closed"
    assert detail.merged is True


def test_fake_close_pull_request_marks_closed_unmerged():
    client = FakeGithubClient()
    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )

    client.close_pull_request(created.number, merged=False)

    detail = client.get_pull_request("o/r", number=created.number)
    assert detail.state == "closed"
    assert detail.merged is False


# --- get_pull_request, http ---------------------------------------------------


def test_http_get_pull_request_maps_open_state():
    payload = {
        "number": 7,
        "html_url": "https://github.com/o/r/pull/7",
        "state": "open",
        "merged": False,
        "head": {"label": "o:harness/tsk_1"},
    }
    client = HttpGithubClient("tok", opener=FakeOpener(payload))

    detail = client.get_pull_request("o/r", number=7)

    assert detail.number == 7
    assert detail.state == "open"
    assert detail.merged is False


def test_http_get_pull_request_maps_merged():
    payload = {
        "number": 7,
        "html_url": "https://github.com/o/r/pull/7",
        "state": "closed",
        "merged": True,
    }
    client = HttpGithubClient("tok", opener=FakeOpener(payload))

    detail = client.get_pull_request("o/r", number=7)

    assert detail.state == "closed"
    assert detail.merged is True


def test_http_get_pull_request_maps_closed_unmerged():
    payload = {
        "number": 7,
        "html_url": "https://github.com/o/r/pull/7",
        "state": "closed",
        "merged": False,
    }
    client = HttpGithubClient("tok", opener=FakeOpener(payload))

    detail = client.get_pull_request("o/r", number=7)

    assert detail.state == "closed"
    assert detail.merged is False


def test_http_get_pull_request_uses_the_right_url():
    opener = FakeOpener({"number": 7, "state": "open", "merged": False})
    client = HttpGithubClient("tok", opener=opener)

    client.get_pull_request("o/r", number=7)

    req = opener.requests[0]
    assert req.get_method() == "GET"
    assert req.full_url == "https://api.github.com/repos/o/r/pulls/7"


def test_fake_get_pull_request_reports_merged_state():
    client = FakeGithubClient()
    created = client.create_pull_request(
        "o/r", head="o:harness/tsk_1", base="main", title="T", body="B"
    )

    assert client.get_pull_request("o/r", created.number).merged is False

    client.mark_merged(created.number)

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
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 500, "Server Error", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=ServerErrorOpener())

    with pytest.raises(urllib.error.HTTPError):
        client.get_pull_request("o/r", 7)


# --- PR conflict detection support: PullRequestInfo, list_pull_requests, update_branch ---


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


def test_fake_carries_the_head_repo_so_a_fork_pr_can_be_constructed():
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "main", "sha1", "main", "dirty", head_repo="fork/r")
    )
    client.add_pull_request(
        PullRequestInfo(2, "u2", "main", "sha2", "main", "dirty", head_repo="o/r")
    )

    assert [pr.head_repo for pr in client.list_pull_requests("o/r")] == [
        "fork/r",
        "o/r",
    ]


def test_head_repo_defaults_to_unknown():
    info = PullRequestInfo(1, "u", "b", "s", "main", "dirty")

    assert info.head_repo == ""


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
        "head": {"sha": "abc123", "repo": {"full_name": "o/r"}},
        "base": {"ref": "main"},
        "mergeable_state": "behind",
    }

    class TieredOpener:
        def __init__(self):
            self.requests = []

        def open(self, request, timeout=None):
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
    assert info.head_repo == "o/r"
    # exactly one list call + one detail call (for the matching PR only)
    assert len(opener.requests) == 2


def test_http_list_pull_requests_asks_for_a_full_page_and_follows_link_next():
    """GitHub's default page is 30 open PRs. Without `per_page` + `Link`
    following, a busy repo (dependabot alone crosses 30) is silently truncated
    and the harness simply never sees the rest — invisible from the outside."""

    def _item(number):
        return {
            "number": number,
            "html_url": f"u{number}",
            "head": {"ref": f"b{number}", "sha": f"s{number}", "repo": {"full_name": "o/r"}},
            "base": {"ref": "main"},
        }

    class PagingOpener:
        def __init__(self):
            self.urls = []

        def open(self, request, timeout=None):
            self.urls.append(request.full_url)
            if "/pulls/" in request.full_url:
                return FakeResponse({"head": {"sha": "x"}, "mergeable_state": "dirty"})
            if "page=2" in request.full_url:
                return FakeResponse([_item(2)])
            return FakeResponse(
                [_item(1)],
                headers={"Link": '<https://api.github.com/x?page=2>; rel="next", '
                                 '<https://api.github.com/x?page=9>; rel="last"'},
            )

    opener = PagingOpener()
    client = HttpGithubClient("tok", opener=opener)

    infos = client.list_pull_requests("o/r")

    assert [i.number for i in infos] == [1, 2]
    assert "per_page=100" in opener.urls[0]
    assert opener.urls[1] == "https://api.github.com/x?page=2"


def test_http_list_pull_requests_stops_when_there_is_no_next_link():
    class SinglePageOpener:
        def __init__(self):
            self.calls = 0

        def open(self, request, timeout=None):
            self.calls += 1
            if "/pulls/" in request.full_url:
                return FakeResponse({"head": {"sha": "x"}, "mergeable_state": "dirty"})
            # `last`/`prev` present, `next` absent — the final page.
            return FakeResponse(
                [
                    {
                        "number": 1,
                        "html_url": "u",
                        "head": {"ref": "b", "sha": "s", "repo": {"full_name": "o/r"}},
                        "base": {"ref": "main"},
                    }
                ],
                headers={"Link": '<https://api.github.com/x?page=1>; rel="prev"'},
            )

    opener = SinglePageOpener()
    client = HttpGithubClient("tok", opener=opener)

    assert len(client.list_pull_requests("o/r")) == 1
    assert opener.calls == 2  # one list page + one detail call, no page 2


def test_http_list_pull_requests_reads_the_head_repo_of_a_fork():
    class TieredOpener:
        def open(self, request, timeout=None):
            if request.full_url.endswith("/pulls/1"):
                return FakeResponse(
                    {
                        "head": {"sha": "x", "repo": {"full_name": "contributor/r"}},
                        "base": {"ref": "main"},
                        "mergeable_state": "dirty",
                    }
                )
            return FakeResponse(
                [
                    {
                        "number": 1,
                        "html_url": "u",
                        "head": {"ref": "main", "repo": {"full_name": "contributor/r"}},
                        "base": {"ref": "main"},
                    }
                ]
            )

    client = HttpGithubClient("tok", opener=TieredOpener())

    [info] = client.list_pull_requests("o/r")

    assert info.head_repo == "contributor/r"


def test_http_list_pull_requests_null_head_repo_reads_as_unknown():
    """GitHub nulls `head.repo` once the fork has been deleted."""

    class TieredOpener:
        def open(self, request, timeout=None):
            if request.full_url.endswith("/pulls/1"):
                return FakeResponse(
                    {
                        "head": {"sha": "x", "repo": None},
                        "base": {"ref": "main"},
                        "mergeable_state": "dirty",
                    }
                )
            return FakeResponse(
                [
                    {
                        "number": 1,
                        "html_url": "u",
                        "head": {"ref": "gone", "repo": None},
                        "base": {"ref": "main"},
                    }
                ]
            )

    client = HttpGithubClient("tok", opener=TieredOpener())

    [info] = client.list_pull_requests("o/r")

    assert info.head_repo == ""


def test_http_list_pull_requests_missing_mergeable_state_is_unknown():
    class TieredOpener:
        def open(self, request, timeout=None):
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
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 422, "Unprocessable", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=NotBehindOpener())
    client.update_branch("o/r", 5)  # must not raise


def test_http_update_branch_other_error_propagates():
    class ServerErrorOpener:
        def open(self, request, timeout=None):
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

    found = client.search_issue_by_marker(
        "o/r", "<!-- harness-heal:tsk_9 -->", label=SELF_HEAL_LABEL
    )
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

    assert (
        client.search_issue_by_marker(
            "o/r", "<!-- harness-heal:tsk_9 -->", label=SELF_HEAL_LABEL
        )
        is None
    )


# --- timeout -----------------------------------------------------------


def test_http_client_defaults_to_a_30_second_timeout():
    opener = FakeOpener({"default_branch": "main"})
    client = HttpGithubClient("tok", opener=opener)

    client.default_branch("o/r")

    assert opener.timeouts == [30.0]


def test_http_client_configured_timeout_reaches_every_call_site():
    opener = FakeOpener([])
    client = HttpGithubClient("tok", opener=opener, timeout=7.5)

    client.list_issues("o/r", label="harness:todo")
    client.add_label("o/r", 1, "harness:queued")

    assert opener.timeouts == [7.5, 7.5]


def test_http_client_raised_timeout_is_not_swallowed():
    class HangingOpener:
        def open(self, request, timeout=None):
            raise TimeoutError("timed out")

    client = HttpGithubClient("tok", opener=HangingOpener())

    with pytest.raises(TimeoutError):
        client.list_issues("o/r", label="harness:todo")


def test_http_client_dead_peer_raises_timeout_within_a_bound_not_forever():
    """A real socket that accepts the connection and never writes back — the
    same shape as a NAT/idle drop killing a GitHub connection silently. Proves
    the configured timeout is honoured by the real `urllib` machinery, not
    just plumbed through as an inert kwarg, without ever waiting anywhere near
    the wedge this regression guards against (observed: ~34 hours)."""
    import socket
    import threading
    import time

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def accept_and_hang():
        try:
            conn, _ = server.accept()
            try:
                # Hold the connection open (never read/write) well past the
                # client's timeout, so it can't be garbage-collected and
                # reset the connection out from under the client's read.
                time.sleep(2.0)
            finally:
                conn.close()
        except OSError:
            pass  # server closed while waiting — fine, test is tearing down

    thread = threading.Thread(target=accept_and_hang, daemon=True)
    thread.start()

    try:
        client = HttpGithubClient(
            "tok", api=f"http://127.0.0.1:{port}", timeout=0.05
        )

        start = time.monotonic()
        with pytest.raises(TimeoutError):
            client.list_issues("o/r", label="harness:todo")
        elapsed = time.monotonic() - start

        assert elapsed < 2.0  # bounded — nowhere near "forever"
    finally:
        server.close()
        thread.join(timeout=1.0)


# --- check runs ------------------------------------------------------------


class FakeTextResponse:
    def __init__(self, text):
        self._body = text.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fake_list_check_runs_is_keyed_by_sha():
    client = FakeGithubClient([])
    client.add_check_run("abc", CheckRun(1, "pytest", "failure", "u1"))
    client.add_check_run("def", CheckRun(2, "lint", "success", "u2"))

    assert [r.id for r in client.list_check_runs("o/r", "abc")] == [1]
    assert client.list_check_runs("o/r", "zzz") == []


def test_fake_check_run_log_defaults_to_empty():
    client = FakeGithubClient([])
    client.set_check_run_log(1, "boom")

    assert client.check_run_log("o/r", 1) == "boom"
    assert client.check_run_log("o/r", 99) == ""


def test_http_list_check_runs_reads_the_commit_endpoint():
    payload = {
        "check_runs": [
            {"id": 7, "name": "pytest (3.12)", "conclusion": "failure",
             "html_url": "https://gh/run/7"},
            {"id": 8, "name": "lint", "conclusion": "success",
             "html_url": "https://gh/run/8"},
        ]
    }
    opener = FakeOpener(payload)
    client = HttpGithubClient("tok", opener=opener)

    runs = client.list_check_runs("o/r", "abc123")

    assert [(r.id, r.name, r.conclusion, r.url) for r in runs] == [
        (7, "pytest (3.12)", "failure", "https://gh/run/7"),
        (8, "lint", "success", "https://gh/run/8"),
    ]
    req = opener.requests[0]
    assert req.get_method() == "GET"
    assert req.full_url == "https://api.github.com/repos/o/r/commits/abc123/check-runs"


def test_http_check_run_log_returns_plain_text():
    class TextOpener:
        def __init__(self):
            self.requests = []

        def open(self, request):
            self.requests.append(request)
            return FakeTextResponse("line one\nline two\n")

    opener = TextOpener()
    client = HttpGithubClient("tok", opener=opener)

    assert client.check_run_log("o/r", 7) == "line one\nline two\n"
    assert opener.requests[0].full_url == (
        "https://api.github.com/repos/o/r/actions/jobs/7/logs"
    )


def test_a_cross_host_redirect_drops_the_github_credential():
    """The Actions log endpoint 302s to Azure Blob Storage, whose SAS token is
    already in the query string. urllib's stock handler replays the original
    `Authorization` to the new host and Azure answers `401 Server failed to
    authenticate` — which is not in `check_run_log`'s (404, 410) allowlist, so
    it raises, `GithubUnhealthyPrsCheck` swallows it per-PR, and every red PR
    is skipped. Measured against the live service: every failing-check fetch
    401'd, so the feature's main path never ran once."""
    handler = _StripCrossHostAuth()
    request = urllib.request.Request(
        "https://api.github.com/repos/o/r/actions/jobs/7/logs",
        headers={"Authorization": "Bearer tok", "Accept": "text/plain"},
    )

    redirected = handler.redirect_request(
        request,
        io.BytesIO(b""),
        302,
        "Found",
        {},
        "https://productionresultssa0.blob.core.windows.net/actions-results/x?sig=y",
    )

    assert redirected.get_header("Authorization") is None
    # Only the credential is dropped — the rest of the request is untouched.
    assert redirected.get_header("Accept") == "text/plain"


def test_a_same_host_redirect_keeps_the_credential():
    """The rule is cross-*host*, not "any redirect": an api.github.com →
    api.github.com hop still needs the token or every such call breaks."""
    handler = _StripCrossHostAuth()
    request = urllib.request.Request(
        "https://api.github.com/repos/o/r/issues",
        headers={"Authorization": "Bearer tok"},
    )

    redirected = handler.redirect_request(
        request, io.BytesIO(b""), 301, "Moved", {},
        "https://api.github.com/repositories/1/issues",
    )

    assert redirected.get_header("Authorization") == "Bearer tok"


def test_the_default_opener_strips_cross_host_credentials():
    """The handler is useless unless the opener the real client builds by
    default actually carries it — the injected test openers never exercise
    urllib's redirect path at all, which is why this went out undetected."""
    client = HttpGithubClient("tok")

    assert any(
        isinstance(handler, _StripCrossHostAuth)
        for handler in client._opener.handlers
    )


def test_http_check_run_log_404_is_empty_string():
    class GoneOpener:
        def open(self, request):
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {}, io.BytesIO(b"")
            )

    client = HttpGithubClient("tok", opener=GoneOpener())

    assert client.check_run_log("o/r", 7) == ""


def test_fake_labels_apply_to_pull_requests_too():
    client = FakeGithubClient([])
    client.add_pull_request(
        PullRequestInfo(
            number=42, url="u", head_branch="b", head_sha="s",
            base_branch="main", mergeable_state="unstable",
        )
    )

    client.add_label("o/r", 42, "harness:autofix-1")
    assert client.list_pull_requests("o/r")[0].labels == ("harness:autofix-1",)

    client.remove_label("o/r", 42, "harness:autofix-1")
    assert client.list_pull_requests("o/r")[0].labels == ()
