import json

from harness.drivers.fake_forge import FakeForge
from harness.models import Task


def _make_task(task_id="tsk_1"):
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
    )


def test_open_pull_request_writes_file_and_returns_details(tmp_path):
    forge = FakeForge(tmp_path)
    task = _make_task()

    pr = forge.open_pull_request(
        task, branch="harness/tsk_1", title="Task tsk_1", body="PR body"
    )

    assert pr.number == 1
    assert pr.branch == "harness/tsk_1"
    assert pr.title == "Task tsk_1"
    assert pr.url == f"file://{tmp_path}/prs.json#1"

    records = json.loads((tmp_path / "prs.json").read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["branch"] == "harness/tsk_1"
    assert records[0]["body"] == "PR body"


def test_second_open_for_same_branch_is_idempotent(tmp_path):
    forge = FakeForge(tmp_path)
    task = _make_task()

    first = forge.open_pull_request(
        task, branch="harness/tsk_1", title="Task tsk_1", body="body"
    )
    second = forge.open_pull_request(
        task, branch="harness/tsk_1", title="different title", body="different body"
    )

    assert second.number == first.number
    records = json.loads((tmp_path / "prs.json").read_text(encoding="utf-8"))
    assert len(records) == 1


def test_distinct_branches_get_distinct_numbers(tmp_path):
    forge = FakeForge(tmp_path)

    first = forge.open_pull_request(
        _make_task("tsk_1"), branch="harness/tsk_1", title="a", body="a"
    )
    second = forge.open_pull_request(
        _make_task("tsk_2"), branch="harness/tsk_2", title="b", body="b"
    )

    assert first.number == 1
    assert second.number == 2
    records = json.loads((tmp_path / "prs.json").read_text(encoding="utf-8"))
    assert len(records) == 2


def test_open_issue_writes_file_and_returns_details(tmp_path):
    forge = FakeForge(tmp_path)
    task = _make_task()

    issue = forge.open_issue(task, title="harness bug", body="issue body")

    assert issue.number == 1
    assert issue.title == "harness bug"
    assert issue.url == f"file://{tmp_path}/issues.json#1"

    records = json.loads((tmp_path / "issues.json").read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["task_id"] == "tsk_1"
    assert records[0]["body"] == "issue body"


def test_second_open_issue_for_same_task_is_idempotent(tmp_path):
    forge = FakeForge(tmp_path)
    task = _make_task()

    first = forge.open_issue(task, title="harness bug", body="body")
    second = forge.open_issue(task, title="different title", body="different body")

    assert second.number == first.number
    records = json.loads((tmp_path / "issues.json").read_text(encoding="utf-8"))
    assert len(records) == 1


def test_distinct_tasks_get_distinct_issue_numbers(tmp_path):
    forge = FakeForge(tmp_path)

    first = forge.open_issue(_make_task("tsk_1"), title="a", body="a")
    second = forge.open_issue(_make_task("tsk_2"), title="b", body="b")

    assert first.number == 1
    assert second.number == 2
    records = json.loads((tmp_path / "issues.json").read_text(encoding="utf-8"))
    assert len(records) == 2
