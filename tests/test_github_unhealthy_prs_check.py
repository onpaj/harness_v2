"""GithubUnhealthyPrsCheck — PR triage as a Check (no network)."""

from __future__ import annotations

from pathlib import Path

from harness.drivers.github_client import CheckRun, FakeGithubClient, PullRequestInfo
from harness.drivers.github_unhealthy_prs_check import GithubUnhealthyPrsCheck
from harness.drivers.memory import MemoryRepositoryRegistry


def _registry_and_slugs():
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})
    slugs = {Path("/repos/harness_v2"): "onpaj/harness_v2"}
    return registry, slugs


def _pr(number, state, *, head="harness/tsk_1", sha="abc123", base="main",
        labels=(), draft=False):
    return PullRequestInfo(
        number=number,
        url=f"https://gh/pr/{number}",
        head_branch=head,
        head_sha=sha,
        base_branch=base,
        mergeable_state=state,
        labels=labels,
        draft=draft,
    )


def _check(client, **kwargs):
    registry, slugs = _registry_and_slugs()
    return GithubUnhealthyPrsCheck(
        client=client, registry=registry, slug_of=slugs.get, **kwargs
    )


def test_dirty_pr_emits_a_conflict_brief():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(85, "dirty", head="feature/x", sha="3035f7d"))

    (obs,) = _check(client).evaluate()

    assert obs.state_key == "onpaj/harness_v2:85:3035f7d"
    assert obs.repository == "harness_v2"
    assert obs.data["branch"] == "feature/x"
    assert obs.data["title"] == "unblock PR #85"
    assert obs.data["source"] == {
        "kind": "pull-request-health",
        "repo": "onpaj/harness_v2",
        "pr": 85,
        "url": "https://gh/pr/85",
        "base": "main",
    }
    assert obs.data["problem"]["conflicted"] is True
    assert obs.data["problem"]["failing_checks"] == []


def test_unstable_pr_emits_failing_checks_with_tailed_logs():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(7, "unstable", sha="deadbee"))
    client.add_check_run("deadbee", CheckRun(1, "pytest", "failure", "https://gh/run/1"))
    client.add_check_run("deadbee", CheckRun(2, "lint", "success", "https://gh/run/2"))
    client.set_check_run_log(1, "a\nb\nc\nd\n")

    (obs,) = _check(client, log_tail_lines=2).evaluate()

    problem = obs.data["problem"]
    assert problem["conflicted"] is False
    assert problem["failing_checks"] == [
        {"name": "pytest", "url": "https://gh/run/1", "log_tail": "c\nd"}
    ]


def test_dirty_pr_that_is_also_red_reports_both():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(9, "dirty", sha="s9"))
    client.add_check_run("s9", CheckRun(3, "build", "timed_out", "https://gh/run/3"))
    client.set_check_run_log(3, "timeout")

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["conflicted"] is True
    assert [c["name"] for c in obs.data["problem"]["failing_checks"]] == ["build"]


def test_behind_pr_is_updated_and_emits_no_task():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(42, "behind"))

    assert _check(client).evaluate() == []
    assert client.updated_branches == [("onpaj/harness_v2", 42)]


def test_clean_pr_is_left_to_automerge():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(1, "clean"))

    assert _check(client).evaluate() == []
    assert client.updated_branches == []


def test_blocked_pr_awaiting_review_is_skipped():
    # `blocked` with no failing check-run means a required review is missing —
    # nothing an agent can supply.
    client = FakeGithubClient([])
    client.add_pull_request(_pr(2, "blocked", sha="s2"))
    client.add_check_run("s2", CheckRun(4, "pytest", "success", "u"))

    assert _check(client).evaluate() == []


def test_unknown_state_with_no_failures_is_skipped():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(3, "unknown", sha="s3"))

    assert _check(client).evaluate() == []


def test_cancelled_and_skipped_are_not_failures():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(4, "unstable", sha="s4"))
    client.add_check_run("s4", CheckRun(5, "a", "cancelled", "u"))
    client.add_check_run("s4", CheckRun(6, "b", "skipped", "u"))

    assert _check(client).evaluate() == []


def test_skip_label_and_draft_and_give_up_label_are_ignored():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(10, "dirty", sha="a", labels=("harness:no-autofix",)))
    client.add_pull_request(_pr(11, "dirty", sha="b", labels=("harness:needs-human",)))
    client.add_pull_request(_pr(12, "dirty", sha="c", draft=True))

    assert _check(client).evaluate() == []


def test_head_prefix_narrows_the_scan():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(20, "dirty", head="feature/x", sha="s20"))
    client.add_pull_request(_pr(21, "dirty", head="harness/y", sha="s21"))

    obs = _check(client, head_prefix="harness/").evaluate()

    assert [o.data["branch"] for o in obs] == ["harness/y"]


def test_missing_log_degrades_to_none_rather_than_skipping():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(30, "unstable", sha="s30"))
    client.add_check_run("s30", CheckRun(9, "third-party", "failure", "u"))
    # no set_check_run_log → the fake returns ""

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["failing_checks"][0]["log_tail"] is None


def test_body_renders_the_brief_for_the_prompt():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(40, "unstable", sha="s40"))
    client.add_check_run("s40", CheckRun(11, "pytest", "failure", "u"))
    client.set_check_run_log(11, "AssertionError: nope")

    (obs,) = _check(client).evaluate()

    body = obs.data["body"]
    assert "pytest" in body
    assert "AssertionError: nope" in body
    assert "attempt 1 of 3" in body


def test_one_bad_pr_does_not_sink_the_tick():
    class Exploding(FakeGithubClient):
        def list_check_runs(self, repo, sha):
            if sha == "boom":
                raise RuntimeError("500")
            return super().list_check_runs(repo, sha)

    client = Exploding([])
    client.add_pull_request(_pr(50, "unstable", sha="boom"))
    client.add_pull_request(_pr(51, "dirty", sha="fine"))

    obs = _check(client).evaluate()

    assert [o.data["source"]["pr"] for o in obs] == [51]


def test_seen_ledger_suppresses_a_relist_at_the_same_head():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(60, "dirty", sha="head1"))
    check = _check(client)

    assert len(check.evaluate()) == 1
    assert check.evaluate() == []
