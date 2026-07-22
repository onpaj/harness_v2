import pytest

from harness.behaviors.file_issue import FileIssueBehavior
from harness.drivers.github_client import FakeGithubClient
from harness.drivers.github_forge import ForgeError, GithubForge
from harness.drivers.memory import MemoryForge
from harness.models import HistoryEntry, Outcome, Task


def make_task() -> Task:
    return Task(
        id="tsk_healer_1",
        workflow_template="healer",
        created="2026-07-22T10:00:00Z",
        repository="harness_v2",
        status="file_issue",
        data={
            "failed_task": {
                "id": "tsk_original",
                "workflow": "default",
                "step": "development",
                "reason": "behavior raised an exception: boom",
                "repository": "my-target-repo",
            },
        },
        history=(
            HistoryEntry(
                at="t",
                actor="consumer:diagnose",
                from_step="diagnose",
                to_step=None,
                outcome="bug_confirmed",
                summary="Found a bug: the retry loop never terminates.",
            ),
        ),
    )


async def test_files_issue_and_returns_done():
    forge = MemoryForge()
    behavior = FileIssueBehavior(forge=forge)

    result = await behavior.run(make_task())

    assert result.outcome is Outcome.DONE
    assert "filed issue" in result.summary
    assert len(forge.issues) == 1
    assert forge.issues[0].title == "harness bug: task tsk_original failed at step 'development'"


async def test_body_content_via_fake_forge(tmp_path):
    from harness.drivers.fake_forge import FakeForge
    import json

    forge = FakeForge(tmp_path)
    behavior = FileIssueBehavior(forge=forge)

    await behavior.run(make_task())

    records = json.loads((tmp_path / "issues.json").read_text(encoding="utf-8"))
    body = records[0]["body"]
    assert "the retry loop never terminates" in body
    assert "tsk_original" in body
    assert "development" in body


async def test_no_failed_task_data_still_files_an_issue():
    forge = MemoryForge()
    task = Task(
        id="tsk_healer_2",
        workflow_template="healer",
        created="2026-07-22T10:00:00Z",
        status="file_issue",
        data={},
    )
    behavior = FileIssueBehavior(forge=forge)

    result = await behavior.run(task)

    assert result.outcome is Outcome.DONE
    assert forge.issues[0].title == "harness bug found while healing task tsk_healer_2"


async def test_retry_is_idempotent():
    forge = MemoryForge()
    behavior = FileIssueBehavior(forge=forge)
    task = make_task()

    await behavior.run(task)
    await behavior.run(task)

    assert len(forge.issues) == 1


async def test_forge_error_propagates():
    forge = GithubForge(None, slug_of=lambda path: "onpaj/harness_v2")
    behavior = FileIssueBehavior(forge=forge)

    with pytest.raises(ForgeError, match="GITHUB_TOKEN"):
        await behavior.run(make_task())
