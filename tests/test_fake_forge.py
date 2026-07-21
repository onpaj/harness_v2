import json
from dataclasses import replace

from harness.drivers.fake_forge import FakeForge
from harness.models import Task
from harness.ports.forge import PullRequestState


def _make_task(task_id="tsk_1"):
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-20T10:00:00Z",
    )


def _landed_task(forge, task_id="tsk_1"):
    """A task carrying its PR reference, the way LandingBehavior leaves it."""
    task = _make_task(task_id)
    pull = forge.open_pull_request(
        task, branch=f"harness/{task_id}", title="T", body="B"
    )
    return replace(
        task, data={"pr": {"number": pull.number, "url": pull.url, "branch": pull.branch}}
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


def test_pull_request_state_defaults_to_open(tmp_path):
    forge = FakeForge(tmp_path)
    landed = _landed_task(forge)

    assert forge.pull_request_state(landed) is PullRequestState.OPEN


def test_close_pull_request_merged(tmp_path):
    forge = FakeForge(tmp_path)
    landed = _landed_task(forge)

    forge.close_pull_request(landed.data["pr"]["branch"], merged=True)

    assert forge.pull_request_state(landed) is PullRequestState.MERGED


def test_close_pull_request_closed_unmerged(tmp_path):
    forge = FakeForge(tmp_path)
    landed = _landed_task(forge)

    forge.close_pull_request(landed.data["pr"]["branch"], merged=False)

    assert forge.pull_request_state(landed) is PullRequestState.CLOSED


def test_pull_request_state_defaults_missing_fields_on_old_records(tmp_path):
    """A prs.json written before this feature shipped has no state/merged keys —
    that must read as 'still open', not an error."""
    forge = FakeForge(tmp_path)
    task = _make_task()
    forge.open_pull_request(task, branch="harness/tsk_1", title="T", body="B")
    records = json.loads((tmp_path / "prs.json").read_text(encoding="utf-8"))
    del records[0]["state"]
    del records[0]["merged"]
    (tmp_path / "prs.json").write_text(json.dumps(records), encoding="utf-8")
    landed = replace(
        task, data={"pr": {"number": 1, "url": records[0]["url"], "branch": "harness/tsk_1"}}
    )

    assert forge.pull_request_state(landed) is PullRequestState.OPEN
