"""`GithubPullRequestMerger`: the `PullRequestMerger` port against real GitHub.

A thin translation layer over `GithubClient.merge_pull_request`, and the
translation *is* the job: it turns the driver's `PullRequestNotMergeable` into
the port's `MergeRefused` (a benign state of the world the caller settles) and
lets every other exception through as a `MergeError` (a genuine fault that
fails the task). Keeping that split here means `behaviors/merge_pr.py` never
imports a driver and never sees an HTTP status code.

The sha pin is passed straight through — see `ports/pr_merge.py` for why that
is the port's load-bearing guarantee rather than an optimization.
"""

from __future__ import annotations

from harness.drivers.github_client import GithubClient, PullRequestNotMergeable
from harness.ports.pr_merge import MergeError, MergeRef, MergeRefused, PullRequestMerger


class GithubPullRequestMerger(PullRequestMerger):
    def __init__(self, client: GithubClient) -> None:
        self._client = client

    def merge(
        self,
        repo: str,
        number: int,
        *,
        method: str,
        expected_sha: str,
        title: str = "",
        body: str = "",
    ) -> MergeRef:
        try:
            sha = self._client.merge_pull_request(
                repo, number, method=method, sha=expected_sha, title=title, body=body
            )
        except PullRequestNotMergeable as error:
            raise MergeRefused(str(error)) from None
        except MergeError:
            raise
        except Exception as error:  # noqa: BLE001 - every other fault is a MergeError
            raise MergeError(f"merging {repo}#{number} failed: {error}") from None
        return MergeRef(sha=sha, url=f"https://github.com/{repo}/pull/{number}")
