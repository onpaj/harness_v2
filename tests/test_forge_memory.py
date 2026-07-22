from dataclasses import replace

from harness.drivers.memory import MemoryForge
from harness.models import Task
from harness.ports.forge import PullRequestState


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
    assert pull.repo == "memory/harness/tsk_1"
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


def _landed(forge: MemoryForge) -> Task:
    pull = forge.open_pull_request(
        make_task(), branch="harness/tsk_1", title="t", body="b"
    )
    return replace(
        make_task(),
        data={"pr": {"number": pull.number, "url": pull.url, "branch": pull.branch}},
    )


def test_pull_request_state_defaults_to_open():
    forge = MemoryForge()
    landed = _landed(forge)

    assert forge.pull_request_state(landed) is PullRequestState.OPEN


def test_close_marks_merged():
    forge = MemoryForge()
    landed = _landed(forge)

    forge.close("harness/tsk_1", merged=True)

    assert forge.pull_request_state(landed) is PullRequestState.MERGED


def test_close_marks_closed_unmerged():
    forge = MemoryForge()
    landed = _landed(forge)

    forge.close("harness/tsk_1", merged=False)

    assert forge.pull_request_state(landed) is PullRequestState.CLOSED
