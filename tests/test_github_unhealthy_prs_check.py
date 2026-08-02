"""GithubUnhealthyPrsCheck — PR triage as a Check (no network)."""

from __future__ import annotations

from pathlib import Path

from harness.drivers.github_client import CheckRun, FakeGithubClient, PullRequestInfo
from harness.drivers.github_unhealthy_prs_check import (
    DEFAULT_GIVE_UP_LABEL,
    GithubUnhealthyPrsCheck,
)
from harness.drivers.memory import MemoryRepositoryRegistry


def _registry_and_slugs():
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})
    slugs = {Path("/repos/harness_v2"): "onpaj/harness_v2"}
    return registry, slugs


def _pr(number, state, *, head="harness/tsk_1", sha="abc123", base="main",
        labels=(), draft=False, head_repo="onpaj/harness_v2"):
    return PullRequestInfo(
        number=number,
        url=f"https://gh/pr/{number}",
        head_branch=head,
        head_sha=sha,
        base_branch=base,
        mergeable_state=state,
        labels=labels,
        draft=draft,
        head_repo=head_repo,
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


def test_a_swallowed_triage_failure_is_reported_on_stderr(capsys):
    """Isolation must not be silence: a systemic fault (a revoked token, a rate
    limit) fails every PR, and with `head_prefix: ""` that is every open PR in
    every repo — the process would otherwise go quiet with a green board."""

    class Exploding(FakeGithubClient):
        def list_check_runs(self, repo, sha):
            raise RuntimeError("401 Bad credentials")

    client = Exploding([])
    client.add_pull_request(_pr(52, "unstable", sha="boom"))

    assert _check(client).evaluate() == []

    err = capsys.readouterr().err
    assert "warning: github-unhealthy-prs could not triage" in err
    assert "#52" in err
    assert "RuntimeError: 401 Bad credentials" in err


def test_seen_ledger_suppresses_a_relist_at_the_same_head():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(60, "dirty", sha="head1"))
    check = _check(client)

    assert len(check.evaluate()) == 1
    assert check.evaluate() == []


def test_first_emit_stamps_attempt_one_against_the_head_sha():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(70, "dirty", sha="s70abcdef"))

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["attempt"] == 1
    assert client.list_pull_requests("o/r")[0].labels == ("harness:autofix-1@s70abcd",)


def test_a_new_head_sha_rolls_the_attempt_forward():
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(71, "dirty", sha="s71", labels=("harness:autofix-1@older12",))
    )

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["attempt"] == 2
    assert client.list_pull_requests("o/r")[0].labels == ("harness:autofix-2@s71",)


def test_re_triaging_an_unchanged_head_does_not_burn_an_attempt():
    """The restart case, and the one that would have caught the bug.

    Five *fresh* check instances over one unchanged PR — each stands for a
    process restart, so the in-process `_seen` ledger is empty every time and
    the label on the PR is the only memory. The attempt must not advance, and
    the PR must never reach `harness:needs-human`.
    """
    client = FakeGithubClient([])
    client.add_pull_request(_pr(77, "dirty", sha="3035f7dcafe"))

    runs = [
        [o.data["problem"]["attempt"] for o in _check(client).evaluate()]
        for _ in range(5)
    ]

    assert runs == [[1], [1], [1], [1], [1]]
    assert client.list_pull_requests("o/r")[0].labels == ("harness:autofix-1@3035f7d",)


def test_re_triaging_an_unchanged_head_writes_no_label_at_all():
    calls: list[tuple[str, str]] = []

    class Recording(FakeGithubClient):
        def add_label(self, repo, number, label):
            calls.append(("add", label))
            super().add_label(repo, number, label)

        def remove_label(self, repo, number, label):
            calls.append(("remove", label))
            super().remove_label(repo, number, label)

    client = Recording([])
    client.add_pull_request(
        _pr(78, "dirty", sha="3035f7dcafe", labels=("harness:autofix-2@3035f7d",))
    )

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["attempt"] == 2
    assert calls == []


def test_the_fresh_label_is_added_before_the_stale_one_is_removed():
    """Add-then-remove, so a failure between the two leaves *two* counters
    rather than none — never a silently reset budget."""
    calls: list[tuple[str, str]] = []

    class Recording(FakeGithubClient):
        def add_label(self, repo, number, label):
            calls.append(("add", label))
            super().add_label(repo, number, label)

        def remove_label(self, repo, number, label):
            calls.append(("remove", label))
            super().remove_label(repo, number, label)

    client = Recording([])
    client.add_pull_request(
        _pr(79, "dirty", sha="newhead1", labels=("harness:autofix-1@oldhead",))
    )

    _check(client).evaluate()

    assert calls == [
        ("add", "harness:autofix-2@newhead"),
        ("remove", "harness:autofix-1@oldhead"),
    ]


def test_two_counter_labels_resolve_to_the_maximum():
    """The transient state add-then-remove can leave behind. Taking the first
    match would be arbitrary — `labels` is an unordered tuple."""
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(
            80,
            "dirty",
            sha="s80",
            labels=("harness:autofix-1@oldhead", "harness:autofix-2@older12"),
        )
    )

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["attempt"] == 3
    assert set(client.list_pull_requests("o/r")[0].labels) == {"harness:autofix-3@s80"}


def test_budget_exhausted_labels_needs_human_and_emits_nothing():
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(72, "dirty", sha="s72", labels=("harness:autofix-3@oldhead",))
    )

    assert _check(client).evaluate() == []
    assert "harness:needs-human" in client.list_pull_requests("o/r")[0].labels


def test_max_attempts_is_configurable():
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(73, "dirty", sha="s73", labels=("harness:autofix-1@oldhead",))
    )

    assert _check(client, max_attempts=1).evaluate() == []
    assert "harness:needs-human" in client.list_pull_requests("o/r")[0].labels


def test_the_last_attempt_may_be_re_triaged_at_its_own_head():
    """Attempt 3 of 3 at the head the label already names is the restart case,
    not a fourth attempt — it must re-emit, not give up."""
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(81, "dirty", sha="s81", labels=("harness:autofix-3@s81",))
    )

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["attempt"] == 3
    assert "harness:needs-human" not in client.list_pull_requests("o/r")[0].labels


def test_a_malformed_attempt_label_is_treated_as_zero():
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(74, "dirty", sha="s74", labels=("harness:autofix-oops",))
    )

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["attempt"] == 1


def test_a_legacy_unstamped_label_names_no_head_and_so_bumps():
    """`harness:autofix-2` is what the previous version of this check wrote.
    It names no head sha, so it can never match the current one: the attempt
    it records is honoured and the next triage bumps, exactly as before."""
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(82, "dirty", sha="s82", labels=("harness:autofix-2",))
    )

    (obs,) = _check(client).evaluate()

    assert obs.data["problem"]["attempt"] == 3
    assert client.list_pull_requests("o/r")[0].labels == ("harness:autofix-3@s82",)


def test_a_fork_pr_is_never_touched():
    """A fork PR's head branch lives in the contributor's repo; attaching a
    worktree to it would check out — and push to — the *base* repo's branch of
    the same name. The commonest shape is a fork's own `main`."""
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(83, "dirty", head="main", sha="s83", head_repo="contributor/harness_v2")
    )

    assert _check(client).evaluate() == []
    assert client.list_pull_requests("o/r")[0].labels == ()


def test_a_pr_whose_head_repo_is_unknown_is_skipped():
    """GitHub reports a null head repo once the fork has been deleted. Fail
    closed: an unattributable head is never worked on."""
    client = FakeGithubClient([])
    client.add_pull_request(_pr(84, "dirty", sha="s84", head_repo=""))

    assert _check(client).evaluate() == []


def test_no_label_is_written_for_a_behind_or_clean_pr():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(75, "behind", sha="s75"))
    client.add_pull_request(_pr(76, "clean", sha="s76"))

    _check(client).evaluate()

    for pull in client.list_pull_requests("o/r"):
        assert pull.labels == ()


def test_the_fork_guard_compares_the_two_slugs_case_insensitively():
    """`slug` comes verbatim from `remote.origin.url`, `head_repo` from
    GitHub's canonical `full_name`. A clone made with
    `git clone git@github.com:OnPaj/Harness_v2.git` yields a differently-cased
    local slug, and a case-sensitive compare then fails the guard for *every*
    PR of that repo — no observations, no warning, a green board. GitHub repo
    paths are case-insensitive, so every API call keeps working, which is
    exactly what makes it invisible."""
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})
    client = FakeGithubClient([])
    client.add_pull_request(_pr(90, "dirty", sha="s90", head_repo="onpaj/harness_v2"))
    check = GithubUnhealthyPrsCheck(
        client=client, registry=registry, slug_of=lambda path: "OnPaj/Harness_v2"
    )

    (obs,) = check.evaluate()

    assert obs.data["source"]["pr"] == 90


def test_a_genuine_fork_is_still_skipped_when_the_casing_differs():
    registry = MemoryRepositoryRegistry({"harness_v2": Path("/repos/harness_v2")})
    client = FakeGithubClient([])
    client.add_pull_request(
        _pr(91, "dirty", sha="s91", head_repo="Contributor/Harness_v2")
    )
    check = GithubUnhealthyPrsCheck(
        client=client, registry=registry, slug_of=lambda path: "OnPaj/Harness_v2"
    )

    assert check.evaluate() == []


# --- the give-up label travels with the task (ADR-0026) ----------------------


def test_the_observation_carries_the_give_up_label_the_check_would_read():
    """The agent's give-up is the only thing that can reach `harness:needs-human`
    on the `stuck` path — the head sha never moves there, so no attempt is ever
    spent and the check would re-mint the same task forever. The behavior
    applies the label, and it must be *this* check's configured one, not a
    constant duplicated in the wiring."""
    client = FakeGithubClient([])
    client.add_pull_request(_pr(92, "dirty", sha="s92"))

    (obs,) = _check(client, give_up_label="team:needs-human").evaluate()

    assert obs.data["give_up_label"] == "team:needs-human"


def test_the_give_up_label_defaults_to_the_module_constant():
    client = FakeGithubClient([])
    client.add_pull_request(_pr(93, "dirty", sha="s93"))

    (obs,) = _check(client).evaluate()

    assert obs.data["give_up_label"] == DEFAULT_GIVE_UP_LABEL


# --- an attempt is spent only once the brief is actually assembled -----------


def test_a_log_fetch_failure_spends_no_attempt_and_is_retried():
    """`check_run_log` is a network call that can 5xx. Bumping the label (and
    marking the PR seen) before it means a transient failure burns an attempt
    with zero work done and is not retried until the next restart."""
    fail = {"now": True}

    class Flaky(FakeGithubClient):
        def check_run_log(self, repo, check_run_id):
            if fail["now"]:
                raise RuntimeError("502 Bad Gateway")
            return super().check_run_log(repo, check_run_id)

    client = Flaky([])
    client.add_pull_request(_pr(94, "unstable", sha="s94"))
    client.add_check_run("s94", CheckRun(1, "pytest", "failure", "https://gh/run/1"))
    client.set_check_run_log(1, "boom")
    check = _check(client)

    assert check.evaluate() == []
    assert client.list_pull_requests("o/r")[0].labels == ()

    fail["now"] = False
    (obs,) = check.evaluate()

    assert obs.data["problem"]["attempt"] == 1
    assert client.list_pull_requests("o/r")[0].labels == ("harness:autofix-1@s94",)
