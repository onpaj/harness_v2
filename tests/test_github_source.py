"""GithubTaskSource — issue → task, status → label."""

from harness.drivers.github_client import FakeGithubClient, Issue
from harness.drivers.github_source import GithubLabelReflector, GithubTaskSource
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
        [Issue(1, "Fix bug", "details", "https://gh/o/r/issues/1", ("harness:todo",))]
    )
    source = build_source(client)

    [task] = source.poll()

    assert _labels(client, 1) == {"harness:queued"}
    assert task.dedup_key == "github:o/r:1"
    assert task.data["source"] == {
        "kind": "github",
        "repo": "o/r",
        "issue": 1,
        "url": "https://gh/o/r/issues/1",
    }
    assert task.data["title"] == "Fix bug"
    assert task.data["body"] == "details"
    assert task.repository == "/repos/r"
    assert task.worktree == f"/wt/{task.id}"


def test_poll_with_step_builds_workflow_less_task():
    client = FakeGithubClient(
        [Issue(1, "Fix bug", "details", "https://gh/o/r/issues/1", ("harness:todo",))]
    )
    source = build_source(client, workflow=None, step="development")

    [task] = source.poll()

    assert task.workflow_template is None
    assert task.step == "development"


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
    """Simulates GitHub's read-after-write lag: `list_issues` returns the issue
    under `harness:todo` even after `remove_label` — the label swap hasn't
    propagated to the list yet. Without dedup, `poll()` would claim the same
    issue repeatedly."""

    def remove_label(self, repo, number, label):  # noqa: D401 - lag: no-op
        pass


def test_poll_dedups_claimed_issue_despite_label_lag():
    client = LaggyGithubClient(
        [Issue(1, "Fix", "", "u1", ("harness:todo",))]
    )
    source = build_source(client)

    first = source.poll()
    second = source.poll()  # list still returns #1 under harness:todo (lag)

    assert len(first) == 1
    assert second == []  # #1 was already claimed → not ingested a second time


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

    source.report_progress(task, Progress(step="plan"))  # not in step_labels

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


def test_task_from_another_repo_is_not_mine():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:todo",))])
    source = build_source(client)  # repo="o/r"
    foreign = Task(
        id="tsk_x",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        repository="/repos/other",
        worktree="/wt/tsk_x",
        data={
            "source": {
                "kind": "github",
                "repo": "o/other",  # a DIFFERENT github repo
                "issue": 1,
                "url": "u",
            }
        },
    )

    source.report_progress(foreign, Progress(step="development"))
    source.finish(foreign, FinishResult(ok=True))

    assert _labels(client, 1) == {"harness:todo"}  # untouched — not this source's repo


def build_reflector(client, **kwargs):
    return GithubLabelReflector(
        client=client,
        repo="o/r",
        step_labels={"development": "harness:coding"},
        **kwargs,
    )


def _task(number, *, repo="o/r"):
    return Task(
        id="tsk_x",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        repository="/repos/r",
        worktree="/wt/tsk_x",
        data={"source": {"kind": "github", "repo": repo, "issue": number, "url": "u"}},
    )


def test_reflector_poll_is_always_empty():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:queued",))])
    reflector = build_reflector(client)

    assert reflector.poll() == []


def test_reflector_report_progress_known_step_sets_step_label():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:queued",))])
    reflector = build_reflector(client)

    reflector.report_progress(_task(1), Progress(step="development"))

    assert _labels(client, 1) == {"harness:coding"}


def test_reflector_report_progress_unknown_step_leaves_labels_unchanged():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:queued",))])
    reflector = build_reflector(client)

    reflector.report_progress(_task(1), Progress(step="plan"))  # not in step_labels

    assert _labels(client, 1) == {"harness:queued"}


def test_reflector_finish_ok_sets_pr_label():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:queued",))])
    reflector = build_reflector(client)
    reflector.report_progress(_task(1), Progress(step="development"))

    reflector.finish(_task(1), FinishResult(ok=True))

    assert _labels(client, 1) == {"harness:pr-open"}


def test_reflector_finish_not_ok_sets_failed_label():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:queued",))])
    reflector = build_reflector(client)

    reflector.finish(_task(1), FinishResult(ok=False))

    assert _labels(client, 1) == {"harness:failed"}


def test_reflector_double_report_progress_is_idempotent():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:queued",))])
    reflector = build_reflector(client)

    reflector.report_progress(_task(1), Progress(step="development"))
    reflector.report_progress(_task(1), Progress(step="development"))

    assert _labels(client, 1) == {"harness:coding"}


def test_reflector_double_finish_is_idempotent():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:queued",))])
    reflector = build_reflector(client)

    reflector.finish(_task(1), FinishResult(ok=True))
    reflector.finish(_task(1), FinishResult(ok=True))

    assert _labels(client, 1) == {"harness:pr-open"}


def test_reflector_ignores_task_without_source():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:queued",))])
    reflector = build_reflector(client)
    foreign = Task(
        id="tsk_x",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        data={},
    )

    reflector.report_progress(foreign, Progress(step="development"))
    reflector.finish(foreign, FinishResult(ok=True))

    assert _labels(client, 1) == {"harness:queued"}


def test_reflector_ignores_task_from_another_repo():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:queued",))])
    reflector = build_reflector(client)  # repo="o/r"

    reflector.report_progress(_task(1, repo="o/other"), Progress(step="development"))
    reflector.finish(_task(1, repo="o/other"), FinishResult(ok=True))

    assert _labels(client, 1) == {"harness:queued"}


def test_reflector_ignores_task_from_foreign_kind():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:queued",))])
    reflector = build_reflector(client)
    foreign = Task(
        id="tsk_x",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        data={"source": {"kind": "slack", "repo": "o/r", "issue": 1, "url": "u"}},
    )

    reflector.report_progress(foreign, Progress(step="development"))
    reflector.finish(foreign, FinishResult(ok=True))

    assert _labels(client, 1) == {"harness:queued"}


def test_reflector_preserves_non_managed_labels():
    client = FakeGithubClient([Issue(1, "Fix", "", "u1", ("harness:queued", "bug"))])
    reflector = build_reflector(client)

    reflector.finish(_task(1), FinishResult(ok=True))

    assert _labels(client, 1) == {"bug", "harness:pr-open"}
