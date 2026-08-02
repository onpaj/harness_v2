"""`GithubPullRequestMerger` — the `PullRequestMerger` port against GitHub.

The class is a translation layer, and the translation is the whole point: a
refusal must arrive as `MergeRefused` (benign, settle) and a fault as
`MergeError` (fail the task). Getting that backwards either fails tasks over
ordinary races or hides real breakage, so it is what these tests pin down.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from harness.drivers.github_client import (
    FakeGithubClient,
    HttpGithubClient,
    PullRequestInfo,
    PullRequestNotMergeable,
)
from harness.drivers.github_pr_merger import GithubPullRequestMerger
from harness.ports.pr_merge import MergeError, MergeRefused


def _client_with_pr(number=85, sha="abc123", state="clean"):
    client = FakeGithubClient([])
    client.add_pull_request(
        PullRequestInfo(
            number=number,
            url=f"https://gh/pr/{number}",
            head_branch="harness/tsk_1",
            head_sha=sha,
            base_branch="main",
            mergeable_state=state,
        )
    )
    return client


def test_merges_and_reports_the_resulting_sha():
    client = _client_with_pr()
    merger = GithubPullRequestMerger(client)

    ref = merger.merge(
        "onpaj/harness_v2",
        85,
        method="squash",
        expected_sha="abc123",
        title="Fix retries",
        body="because",
    )

    assert ref.sha == "merged85"
    assert client.merges == [
        {
            "repo": "onpaj/harness_v2",
            "number": 85,
            "method": "squash",
            "sha": "abc123",
            "title": "Fix retries",
            "body": "because",
        }
    ]


def test_a_moved_head_becomes_a_refusal():
    client = _client_with_pr(sha="newsha")
    merger = GithubPullRequestMerger(client)

    with pytest.raises(MergeRefused, match="head moved"):
        merger.merge("onpaj/harness_v2", 85, method="squash", expected_sha="oldsha")

    assert client.merges == []


def test_a_non_mergeable_state_becomes_a_refusal():
    client = _client_with_pr(state="dirty")
    merger = GithubPullRequestMerger(client)

    with pytest.raises(MergeRefused, match="mergeable_state"):
        merger.merge("onpaj/harness_v2", 85, method="squash", expected_sha="abc123")


def test_an_arbitrary_client_fault_becomes_a_merge_error_not_a_refusal():
    """A refusal settles the task; a fault fails it. A network blip must never
    be mistaken for "GitHub said no"."""

    class BoomClient(FakeGithubClient):
        def merge_pull_request(self, repo, number, **kwargs):
            raise RuntimeError("connection reset")

    merger = GithubPullRequestMerger(BoomClient([]))

    with pytest.raises(MergeError) as caught:
        merger.merge("onpaj/harness_v2", 85, method="squash", expected_sha="abc")
    assert not isinstance(caught.value, MergeRefused)
    assert "connection reset" in str(caught.value)


def test_already_merged_returns_the_existing_merge():
    client = _client_with_pr()
    merger = GithubPullRequestMerger(client)

    first = merger.merge("onpaj/harness_v2", 85, method="squash", expected_sha="abc123")
    second = merger.merge("onpaj/harness_v2", 85, method="squash", expected_sha="abc123")

    assert first.sha == second.sha
    assert len(client.merges) == 1


# --- the HTTP client's own half -------------------------------------------


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Opener:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {"sha": "merged-sha"}
        self.error = error
        self.requests = []

    def open(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return _Response(self.payload)


def test_http_client_pins_the_sha_in_the_request_body():
    """The pin is the port's load-bearing guarantee — a driver that drops it
    would merge unreviewed code while every other test still passed."""
    opener = _Opener()
    client = HttpGithubClient("token", opener=opener)

    sha = client.merge_pull_request(
        "onpaj/harness_v2",
        85,
        method="squash",
        sha="abc123",
        title="Fix retries",
        body="because",
    )

    assert sha == "merged-sha"
    (request,) = opener.requests
    assert request.method == "PUT"
    assert request.full_url.endswith("/repos/onpaj/harness_v2/pulls/85/merge")
    payload = json.loads(request.data)
    assert payload == {
        "merge_method": "squash",
        "sha": "abc123",
        "commit_title": "Fix retries",
        "commit_message": "because",
    }


@pytest.mark.parametrize("code", [405, 409, 422])
def test_http_client_maps_refusal_codes_to_not_mergeable(code):
    opener = _Opener(
        error=urllib.error.HTTPError("url", code, "nope", {}, None)
    )
    client = HttpGithubClient("token", opener=opener)

    with pytest.raises(PullRequestNotMergeable):
        client.merge_pull_request("o/r", 1, method="merge", sha="abc")


def test_http_client_propagates_other_errors():
    opener = _Opener(error=urllib.error.HTTPError("url", 500, "boom", {}, None))
    client = HttpGithubClient("token", opener=opener)

    with pytest.raises(urllib.error.HTTPError):
        client.merge_pull_request("o/r", 1, method="merge", sha="abc")
