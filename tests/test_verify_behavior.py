"""VerifyBehavior: exit code → outcome, output → artifact, worker commits."""

from __future__ import annotations

import pytest

from harness.behaviors.verify import VerifyBehavior
from harness.drivers.memory import (
    MemoryCommandRunner,
    MemoryRepositoryRegistry,
    MemoryWorkspace,
)
from harness.models import DONE, REQUEST_CHANGES, Task
from harness.ports.command import CommandResult, CommandTimeout


def make_task(repository: str | None = "app") -> Task:
    return Task.from_dict(
        {
            "id": "tsk_verify_test",
            "repository": repository,
            "status": "verify",
            "created": "2026-07-24T00:00:00Z",
        }
    )


def make_behavior(tmp_path, runner=None, verify={"app": "make test"}):
    workspace = MemoryWorkspace()
    registry = MemoryRepositoryRegistry({"app": tmp_path}, verify=verify)
    runner = runner or MemoryCommandRunner()
    behavior = VerifyBehavior(workspace=workspace, registry=registry, runner=runner)
    return behavior, workspace, runner


@pytest.mark.asyncio
async def test_green_run_is_done_with_artifact_and_commit(tmp_path):
    runner = MemoryCommandRunner([CommandResult(0, "473 passed\n")])
    behavior, workspace, runner = make_behavior(tmp_path, runner=runner)
    result = await behavior.run(make_task())

    assert result.outcome == DONE
    assert "verify passed" in result.summary
    handle = workspace.handles["tsk_verify_test"]
    # The runner ran the configured command in the worktree.
    assert runner.calls[0]["command"] == "make test"
    assert runner.calls[0]["cwd"] == handle.path
    # The output landed as the step's attempt-indexed artifact, and the
    # worker committed (invariant 9).
    relpaths = [relpath for relpath, _ in handle.writes]
    assert relpaths == [".artifacts/tsk_verify_test/verify-01.md"]
    assert "473 passed" in handle.writes[0][1]
    assert len(handle.commits) == 1


@pytest.mark.asyncio
async def test_red_run_is_request_changes_with_output_tail(tmp_path):
    runner = MemoryCommandRunner([CommandResult(1, "x" * 5000 + "\nFAILED tail")])
    behavior, workspace, runner = make_behavior(tmp_path, runner=runner)
    result = await behavior.run(make_task())

    assert result.outcome == REQUEST_CHANGES
    assert "FAILED tail" in result.summary          # the tail survives
    assert "x" * 5000 not in result.summary          # the head is trimmed
    assert len(workspace.handles["tsk_verify_test"].commits) == 1


@pytest.mark.asyncio
async def test_no_verify_command_is_a_done_noop(tmp_path):
    behavior, workspace, runner = make_behavior(tmp_path, verify={})
    result = await behavior.run(make_task())

    assert result.outcome == DONE
    assert "no verify command" in result.summary
    assert runner.calls == []                        # nothing ran
    assert workspace.handles == {}                   # not even an attach


@pytest.mark.asyncio
async def test_repository_less_task_is_a_done_noop(tmp_path):
    behavior, workspace, runner = make_behavior(tmp_path)
    result = await behavior.run(make_task(repository=None))

    assert result.outcome == DONE
    assert runner.calls == []


@pytest.mark.asyncio
async def test_timeout_bubbles_to_fail_the_task(tmp_path):
    class TimeoutRunner(MemoryCommandRunner):
        async def run(self, command, *, cwd, timeout):
            raise CommandTimeout("too slow")

    behavior, _, _ = make_behavior(tmp_path, runner=TimeoutRunner())
    with pytest.raises(CommandTimeout):
        await behavior.run(make_task())
