"""GithubMergeableCheck — merge-candidate detection as a Check (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.drivers.github_client import FakeGithubClient, PullRequestInfo
from harness.drivers.github_mergeable_check import (
    DEFAULT_SKIP_LABEL,
    GithubMergeableCheck,
)
from harness.drivers.memory import MemoryRepositoryRegistry


def _registry_and_slugs():
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})
    slugs = {Path("/repos/harness_v2"): "onpaj/harness_v2"}
    return registry, slugs


def _pr(
    number,
    state="clean",
    *,
    head="harness/tsk_1",
    sha="abc123",
    base="main",
    labels=(),
    draft=False,
    title="Add the thing",
):
    return PullRequestInfo(
        number=number,
        url=f"https://gh/pr/{number}",
        head_branch=head,
        head_sha=sha,
        base_branch=base,
        mergeable_state=state,
        title=title,
        labels=tuple(labels),
        draft=draft,
    )


def _check(client, **kwargs):
    registry, slugs = _registry_and_slugs()
    return GithubMergeableCheck(
        client=client, registry=registry, slug_of=slugs.get, **kwargs
    )


def test_emits_one_observation_per_clean_pr_with_provenance():
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(85, head="harness/tsk_9", sha="3035f7d", base="main", title="Fix retries")
    )

    obs = _check(client).evaluate()

    assert len(obs) == 1
    (o,) = obs
    assert o.state_key == "onpaj/harness_v2:85:3035f7d"
    assert o.repository == "harness_v2"
    assert o.data["branch"] == "harness/tsk_9"
    assert o.data["title"] == "review PR #85 for automatic merge"
    assert o.data["source"] == {
        "kind": "pull-request",
        "repo": "onpaj/harness_v2",
        "pr": 85,
        "url": "https://gh/pr/85",
        "base": "main",
        "head_sha": "3035f7d",
        "pr_title": "Fix retries",
    }


@pytest.mark.parametrize("state", ["behind", "dirty", "blocked", "unstable", "unknown"])
def test_only_clean_prs_are_candidates(state):
    """`behind`/`dirty` belong to the conflicts check; the rest are states we
    refuse to guess at. The two checks partition the same PR list with no
    overlap."""
    client = FakeGithubClient([])
    client.add_pull_request(_pr(1, state))

    assert _check(client).evaluate() == []


def test_a_draft_pr_is_never_a_candidate():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(1, draft=True))

    assert _check(client).evaluate() == []


def test_the_opt_out_label_vetoes_a_pr():
    """A human's per-PR veto, usable with no config change."""
    client = FakeGithubClient([])
    client.add_pull_request(_pr(1, labels=(DEFAULT_SKIP_LABEL,)))

    assert _check(client).evaluate() == []


def test_the_opt_out_label_is_configurable():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(1, labels=("do-not-merge",)))

    assert _check(client, skip_label="do-not-merge").evaluate() == []
    assert len(_check(client, skip_label="something-else").evaluate()) == 1


def test_only_the_head_prefix_is_watched():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(1, head="feature/hand-written"))

    assert _check(client).evaluate() == []
    assert len(_check(client, head_prefix="feature/").evaluate()) == 1


def test_the_same_head_is_not_re_emitted():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(7, sha="aaa"))
    check = _check(client)

    assert len(check.evaluate()) == 1
    assert check.evaluate() == []


def test_a_new_head_is_a_new_candidate():
    """A re-pushed PR must be reviewed again — the previous review judged
    different code."""
    client = FakeGithubClient([])
    client.add_pull_request(_pr(7, sha="aaa"))
    check = _check(client)
    assert len(check.evaluate()) == 1

    client.add_pull_request(_pr(7, sha="bbb"))
    obs = check.evaluate()

    assert [o.state_key for o in obs] == ["onpaj/harness_v2:7:bbb"]


def test_a_non_github_repo_is_skipped():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(1))
    registry = MemoryRepositoryRegistry({"local": Path("/repos/local")})
    check = GithubMergeableCheck(
        client=client, registry=registry, slug_of=lambda path: None
    )

    assert check.evaluate() == []
