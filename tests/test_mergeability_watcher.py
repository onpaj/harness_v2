"""GithubMergeabilityWatcher — behind PRs auto-update, dirty PRs queue a resolver task."""

from harness.drivers.github_client import FakeGithubClient, PullRequestInfo
from harness.drivers.memory import FakeClock
from harness.drivers.mergeability_watcher import GithubMergeabilityWatcher
from harness.models import Task
from harness.ports.source import FinishResult, Progress


def build_watcher(client, **kwargs):
    return GithubMergeabilityWatcher(
        client=client,
        clock=FakeClock(),
        repo="o/r",
        repository="/repos/r",
        worktree_root="/wt",
        **kwargs,
    )


def test_behind_pr_is_updated_and_yields_no_task():
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "harness/tsk_1", "sha1", "main", "behind")
    )
    watcher = build_watcher(client)

    tasks = watcher.poll()

    assert tasks == []
    assert client.updated_branches == [("o/r", 1)]


def test_dirty_pr_yields_exactly_one_resolver_task():
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "harness/tsk_1", "sha1", "main", "dirty")
    )
    watcher = build_watcher(client)

    [task] = watcher.poll()

    assert task.workflow_template == "resolver"
    assert task.repository == "/repos/r"
    assert task.worktree == f"/wt/{task.id}"
    assert task.data["branch"] == "harness/tsk_1"
    assert task.data["source"] == {
        "kind": "mergeability",
        "repo": "o/r",
        "pr": 1,
        "url": "u1",
        "base": "main",
    }
    assert client.updated_branches == []


def test_dirty_pr_dedup_key_embeds_repo_pr_and_head_sha():
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(7, "u7", "harness/tsk_7", "shaABC", "main", "dirty")
    )
    watcher = build_watcher(client)

    [task] = watcher.poll()

    assert task.dedup_key == "mergeability:o/r:7:shaABC"


def test_reconflicted_pr_after_new_head_sha_gets_a_fresh_dedup_key():
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "harness/tsk_1", "sha_old", "main", "dirty")
    )
    watcher = build_watcher(client)
    [first] = watcher.poll()

    # PR fixed then reconflicted with a new head commit.
    client.add_pull_request(
        PullRequestInfo(1, "u1", "harness/tsk_1", "sha_new", "main", "dirty")
    )
    [second] = watcher.poll()

    assert first.dedup_key != second.dedup_key


def test_clean_and_other_states_are_left_alone():
    client = FakeGithubClient()
    for number, state in enumerate(("clean", "blocked", "unstable", "unknown"), start=1):
        client.add_pull_request(
            PullRequestInfo(number, f"u{number}", f"harness/tsk_{number}", f"sha{number}", "main", state)
        )
    watcher = build_watcher(client)

    tasks = watcher.poll()

    assert tasks == []
    assert client.updated_branches == []


def test_non_harness_pr_is_never_touched():
    client = FakeGithubClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "someone/manual-branch", "sha1", "main", "dirty")
    )
    watcher = build_watcher(client)

    assert watcher.poll() == []


def test_one_bad_pr_does_not_block_the_rest_of_the_tick():
    class FlakyClient(FakeGithubClient):
        def update_branch(self, repo, number):
            if number == 1:
                raise RuntimeError("GitHub 5xx")
            super().update_branch(repo, number)

    client = FlakyClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "harness/tsk_1", "sha1", "main", "behind")
    )
    client.add_pull_request(
        PullRequestInfo(2, "u2", "harness/tsk_2", "sha2", "main", "behind")
    )
    watcher = build_watcher(client)

    tasks = watcher.poll()

    assert tasks == []
    assert client.updated_branches == [("o/r", 2)]


class LabelSpyClient(FakeGithubClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.added: list[tuple[int, str]] = []
        self.removed: list[tuple[int, str]] = []

    def add_label(self, repo, number, label):
        self.added.append((number, label))

    def remove_label(self, repo, number, label):
        self.removed.append((number, label))


def test_report_progress_sets_resolving_label_for_resolve_and_land_steps():
    client = LabelSpyClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "harness/tsk_1", "sha1", "main", "dirty")
    )
    watcher = build_watcher(client)
    [task] = watcher.poll()

    watcher.report_progress(task, Progress(step="resolve"))
    watcher.report_progress(task, Progress(step="land"))

    assert client.added == [(1, "harness:resolving"), (1, "harness:resolving")]


def test_report_progress_unmanaged_step_leaves_label_untouched():
    client = LabelSpyClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "harness/tsk_1", "sha1", "main", "dirty")
    )
    watcher = build_watcher(client)
    [task] = watcher.poll()

    watcher.report_progress(task, Progress(step="end"))

    assert client.added == []


def test_finish_removes_resolving_label():
    client = LabelSpyClient()
    client.add_pull_request(
        PullRequestInfo(1, "u1", "harness/tsk_1", "sha1", "main", "dirty")
    )
    watcher = build_watcher(client)
    [task] = watcher.poll()

    watcher.finish(task, FinishResult(ok=True))

    assert client.removed == [(1, "harness:resolving")]


def test_report_progress_and_finish_are_noop_for_a_foreign_task():
    client = FakeGithubClient()
    watcher = build_watcher(client)
    foreign = Task(
        id="tsk_x",
        workflow_template="default",
        created="2026-07-21T10:00:00Z",
        data={"source": {"kind": "github", "repo": "o/r", "issue": 1}},
    )

    watcher.report_progress(foreign, Progress(step="resolve"))
    watcher.finish(foreign, FinishResult(ok=True))  # must not raise
