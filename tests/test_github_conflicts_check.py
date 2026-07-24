"""GithubConflictsCheck — conflicted-PR detection as a Check (no network)."""

from __future__ import annotations

from pathlib import Path

from harness.drivers.github_client import FakeGithubClient, PullRequestInfo
from harness.drivers.github_conflicts_check import GithubConflictsCheck
from harness.drivers.memory import MemoryRepositoryRegistry


def _registry_and_slugs():
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})
    slugs = {Path("/repos/harness_v2"): "onpaj/harness_v2"}
    return registry, slugs


def _pr(number, state, *, head="harness/tsk_1", sha="abc123", base="main"):
    return PullRequestInfo(
        number=number,
        url=f"https://gh/pr/{number}",
        head_branch=head,
        head_sha=sha,
        base_branch=base,
        mergeable_state=state,
    )


def test_emits_one_observation_per_dirty_pr_with_provenance():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(85, "dirty", head="harness/tsk_9", sha="3035f7d", base="main"))
    registry, slugs = _registry_and_slugs()
    check = GithubConflictsCheck(client=client, registry=registry, slug_of=slugs.get)

    obs = check.evaluate()

    assert len(obs) == 1
    (o,) = obs
    assert o.state_key == "onpaj/harness_v2:85:3035f7d"
    assert o.repository == "harness_v2"
    assert o.data["branch"] == "harness/tsk_9"
    assert o.data["title"] == "resolve merge conflict on PR #85"
    assert o.data["source"] == {
        "kind": "mergeability",
        "repo": "onpaj/harness_v2",
        "pr": 85,
        "url": "https://gh/pr/85",
        "base": "main",
    }


def test_behind_pr_is_updated_and_emits_no_task():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(42, "behind"))
    registry, slugs = _registry_and_slugs()
    check = GithubConflictsCheck(client=client, registry=registry, slug_of=slugs.get)

    obs = check.evaluate()

    assert obs == []
    assert client.updated_branches == [("onpaj/harness_v2", 42)]


def test_clean_and_other_states_are_skipped():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(1, "clean", head="harness/a", sha="s1"))
    client.add_pull_request(_pr(2, "blocked", head="harness/b", sha="s2"))
    client.add_pull_request(_pr(3, "unknown", head="harness/c", sha="s3"))
    registry, slugs = _registry_and_slugs()
    check = GithubConflictsCheck(client=client, registry=registry, slug_of=slugs.get)

    assert check.evaluate() == []
    assert client.updated_branches == []


def test_seen_ledger_suppresses_a_relisted_conflict_within_the_process():
    # The same conflict at the same head must not mint a second task on a second
    # tick (list_pull_requests can re-list it before the task lands).
    client = FakeGithubClient([])
    client.add_pull_request(_pr(85, "dirty", sha="head1"))
    registry, slugs = _registry_and_slugs()
    check = GithubConflictsCheck(client=client, registry=registry, slug_of=slugs.get)

    first = check.evaluate()
    second = check.evaluate()

    assert len(first) == 1
    assert second == []


def test_a_new_head_re_emits_after_the_first_was_seen():
    # Once the PR head advances, the conflict is a new standing reason.
    class MutatingClient(FakeGithubClient):
        pass

    client = MutatingClient([])
    client.add_pull_request(_pr(85, "dirty", sha="head1"))
    registry, slugs = _registry_and_slugs()
    check = GithubConflictsCheck(client=client, registry=registry, slug_of=slugs.get)

    first = check.evaluate()
    client.add_pull_request(_pr(85, "dirty", sha="head2"))  # replaces #85 at a new head
    second = check.evaluate()

    assert [o.state_key for o in first] == ["onpaj/harness_v2:85:head1"]
    assert [o.state_key for o in second] == ["onpaj/harness_v2:85:head2"]


def test_a_failing_update_branch_does_not_drop_the_rest_of_the_tick():
    class FlakyUpdate(FakeGithubClient):
        def update_branch(self, repo, number):
            if number == 42:
                raise RuntimeError("boom")
            return super().update_branch(repo, number)

    client = FlakyUpdate([])
    client.add_pull_request(_pr(42, "behind", head="harness/a", sha="s1"))
    client.add_pull_request(_pr(85, "dirty", head="harness/b", sha="s2"))
    registry, slugs = _registry_and_slugs()
    check = GithubConflictsCheck(client=client, registry=registry, slug_of=slugs.get)

    obs = check.evaluate()

    assert [o.state_key for o in obs] == ["onpaj/harness_v2:85:s2"]


def test_skips_a_pr_outside_head_prefix():
    # Proves the check actually threads its own `head_prefix` constructor
    # argument through to `list_pull_requests` rather than silently dropping
    # it or hardcoding "harness/" — every other fixture PR in this file uses
    # the default "harness/" prefix, so none of them would catch a regression
    # here where a non-default prefix is configured but not honored.
    client = FakeGithubClient([])
    client.add_pull_request(_pr(1, "dirty", head="harness/tsk_1", sha="s1"))
    registry, slugs = _registry_and_slugs()
    check = GithubConflictsCheck(
        client=client, registry=registry, slug_of=slugs.get, head_prefix="release/"
    )

    obs = check.evaluate()

    assert obs == []


def test_skips_a_repo_without_a_github_origin():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(1, "dirty"))
    registry = MemoryRepositoryRegistry(
        {"harness_v2": Path("/repos/harness_v2"), "local": Path("/repos/local")}
    )
    slugs = {Path("/repos/harness_v2"): "onpaj/harness_v2", Path("/repos/local"): None}
    check = GithubConflictsCheck(client=client, registry=registry, slug_of=slugs.get)

    obs = check.evaluate()

    assert [o.repository for o in obs] == ["harness_v2"]
