"""GithubIssuesCheck — the inbound harness:todo scan as a Check (no network)."""

from __future__ import annotations

from pathlib import Path

from harness.drivers.github_client import FakeGithubClient, Issue
from harness.drivers.github_issues_check import GithubIssuesCheck
from harness.drivers.memory import MemoryRepositoryRegistry


def _registry_and_slugs():
    registry = MemoryRepositoryRegistry(
        {"heblo": Path("/repos/heblo"), "harness_v2": Path("/repos/harness_v2")}
    )
    slugs = {
        Path("/repos/heblo"): "onpaj/Anela.Heblo",
        Path("/repos/harness_v2"): "onpaj/harness_v2",
    }
    return registry, slugs


def test_emits_one_observation_per_labelled_issue_with_provenance():
    client = FakeGithubClient(
        [Issue(7, "Fix bug", "the body", "https://gh/i/7", ("harness:todo",))]
    )
    registry, slugs = _registry_and_slugs()
    check = GithubIssuesCheck(client=client, registry=registry, slug_of=slugs.get)

    obs = check.evaluate()

    assert len(obs) == 1
    (o,) = obs
    assert o.state_key == "onpaj/Anela.Heblo:7"
    assert o.repository == "heblo"
    assert o.data["title"] == "Fix bug"
    assert o.data["body"] == "the body"
    assert o.data["source"] == {
        "kind": "github",
        "repo": "onpaj/Anela.Heblo",
        "issue": 7,
        "url": "https://gh/i/7",
    }


def test_claims_by_swapping_the_label():
    client = FakeGithubClient(
        [Issue(7, "t", "b", "u", ("harness:todo", "bug"))]
    )
    registry, slugs = _registry_and_slugs()
    check = GithubIssuesCheck(client=client, registry=registry, slug_of=slugs.get)

    check.evaluate()

    # todo removed, queued added, foreign label untouched.
    remaining = client.list_issues("onpaj/Anela.Heblo", label="harness:queued")
    assert [i.number for i in remaining] == [7]
    assert client.list_issues("onpaj/Anela.Heblo", label="harness:todo") == []
    (issue,) = client.list_issues("onpaj/Anela.Heblo", label="bug")
    assert set(issue.labels) == {"harness:queued", "bug"}


def test_claimed_ledger_suppresses_a_relisted_issue_within_the_process():
    # An issue that (because of read-after-write lag) still lists under the
    # select label on a second evaluate() must not produce a second task.
    class LaggyClient(FakeGithubClient):
        def remove_label(self, repo, number, label):  # no-op: simulate lag
            return None

    client = LaggyClient([Issue(7, "t", "b", "u", ("harness:todo",))])
    registry, slugs = _registry_and_slugs()
    check = GithubIssuesCheck(client=client, registry=registry, slug_of=slugs.get)

    first = check.evaluate()
    second = check.evaluate()

    assert len(first) == 1
    assert second == []


def test_skips_a_repo_without_a_github_origin_and_scans_the_rest():
    client = FakeGithubClient([Issue(1, "t", "b", "u", ("harness:todo",))])
    registry = MemoryRepositoryRegistry(
        {"heblo": Path("/repos/heblo"), "local": Path("/repos/local")}
    )
    slugs = {Path("/repos/heblo"): "onpaj/Anela.Heblo", Path("/repos/local"): None}
    check = GithubIssuesCheck(client=client, registry=registry, slug_of=slugs.get)

    obs = check.evaluate()

    assert [o.repository for o in obs] == ["heblo"]


def test_no_labelled_issues_yields_no_observations():
    client = FakeGithubClient([Issue(1, "t", "b", "u", ("bug",))])
    registry, slugs = _registry_and_slugs()
    check = GithubIssuesCheck(client=client, registry=registry, slug_of=slugs.get)

    assert check.evaluate() == []
