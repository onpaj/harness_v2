"""GithubTaskSource — issue → task, stav → label."""

from harness.drivers.github_client import FakeGithubClient, Issue
from harness.drivers.github_source import GithubTaskSource
from harness.drivers.memory import FakeClock
from harness.models import Task
from harness.ports.source import FinishResult, Progress


def build_source(client, **kwargs):
    return GithubTaskSource(
        client=client,
        clock=FakeClock(),
        repo="o/r",
        repository="/repos/r",
        worktree_root="/wt",
        step_labels={"development": "harness:coding"},
        **kwargs,
    )


def _labels(client, number):
    return set(client._issues[number].labels)


def test_poll_claims_issue_and_builds_task():
    client = FakeGithubClient(
        [Issue(1, "Fix bug", "detaily", "https://gh/o/r/issues/1", ("harness:todo",))]
    )
    source = build_source(client)

    [task] = source.poll()

    assert _labels(client, 1) == {"harness:queued"}
    assert task.data["source"] == {
        "kind": "github",
        "repo": "o/r",
        "issue": 1,
        "url": "https://gh/o/r/issues/1",
    }
    assert task.data["title"] == "Fix bug"
    assert task.data["body"] == "detaily"
    assert task.repository == "/repos/r"
    assert task.worktree == f"/wt/{task.id}"


def test_second_poll_returns_empty():
    client = FakeGithubClient(
        [Issue(1, "Fix bug", "", "u1", ("harness:todo",))]
    )
    source = build_source(client)

    first = source.poll()
    second = source.poll()

    assert len(first) == 1
    assert second == []


class LaggyGithubClient(FakeGithubClient):
    """Simuluje read-after-write lag GitHubu: `list_issues` vrací issue pod
    `harness:todo` i po `remove_label` — swap labelu se do listu ještě
    nepropagoval. Bez dedupu by `poll()` stejné issue claimoval opakovaně."""

    def remove_label(self, repo, number, label):  # noqa: D401 - lag: no-op
        pass


def test_poll_dedups_claimed_issue_despite_label_lag():
    client = LaggyGithubClient(
        [Issue(1, "Fix", "", "u1", ("harness:todo",))]
    )
    source = build_source(client)

    first = source.poll()
    second = source.poll()  # list stále vrací #1 pod harness:todo (lag)

    assert len(first) == 1
    assert second == []  # #1 už bylo claimnuto → podruhé se neingestuje


def test_report_progress_known_step_sets_step_label():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:todo",))])
    source = build_source(client)
    [task] = source.poll()

    source.report_progress(task, Progress(step="development"))

    assert _labels(client, 1) == {"harness:coding"}


def test_report_progress_unknown_step_leaves_labels_unchanged():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:todo",))])
    source = build_source(client)
    [task] = source.poll()  # queued

    source.report_progress(task, Progress(step="plan"))  # není v step_labels

    assert _labels(client, 1) == {"harness:queued"}


def test_finish_ok_sets_pr_label_exactly_one_managed():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:todo",))])
    source = build_source(client)
    [task] = source.poll()
    source.report_progress(task, Progress(step="development"))

    source.finish(task, FinishResult(ok=True))

    assert _labels(client, 1) == {"harness:pr-open"}


def test_finish_not_ok_sets_failed_label():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:todo",))])
    source = build_source(client)
    [task] = source.poll()

    source.finish(task, FinishResult(ok=False))

    assert _labels(client, 1) == {"harness:failed"}


def test_non_managed_labels_are_preserved():
    client = FakeGithubClient(
        [Issue(1, "Fix", "", "u1", ("harness:todo", "bug"))]
    )
    source = build_source(client)
    [task] = source.poll()

    source.finish(task, FinishResult(ok=True))

    assert _labels(client, 1) == {"bug", "harness:pr-open"}


def test_task_without_source_is_noop():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:todo",))])
    source = build_source(client)
    foreign = Task(
        id="tsk_x",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        data={},
    )

    source.report_progress(foreign, Progress(step="development"))
    source.finish(foreign, FinishResult(ok=True))

    assert _labels(client, 1) == {"harness:todo"}
