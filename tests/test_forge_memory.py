from harness.drivers.memory import MemoryForge
from harness.models import Task


def make_task() -> Task:
    return Task(id="tsk_1", workflow_template="default", created="2026-07-19T10:00:00Z")


def test_open_pull_request_records_details():
    forge = MemoryForge()

    pull = forge.open_pull_request(
        make_task(), branch="harness/tsk_1", title="add rate limiting", body="body"
    )

    assert pull.branch == "harness/tsk_1"
    assert pull.title == "add rate limiting"
    assert pull.number == 1
    assert forge.opened == [pull]
    assert forge.bodies["harness/tsk_1"] == "body"


def test_open_pull_request_is_idempotent_per_branch():
    forge = MemoryForge()

    first = forge.open_pull_request(
        make_task(), branch="harness/tsk_1", title="t", body="b1"
    )
    second = forge.open_pull_request(
        make_task(), branch="harness/tsk_1", title="t", body="b2"
    )

    assert second is first
    assert len(forge.opened) == 1


def test_open_issue_records_details():
    forge = MemoryForge()

    issue = forge.open_issue(make_task(), title="harness bug", body="body")

    assert issue.title == "harness bug"
    assert issue.number == 1
    assert forge.issues == [issue]


def test_open_issue_is_idempotent_per_task():
    forge = MemoryForge()

    first = forge.open_issue(make_task(), title="t", body="b1")
    second = forge.open_issue(make_task(), title="t", body="b2")

    assert second is first
    assert len(forge.issues) == 1
