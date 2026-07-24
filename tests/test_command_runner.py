"""The subprocess CommandRunner, driven by real (sub-second) commands.

Deliberate real-subprocess exception to the in-memory rule — the same posture
as test_smoke_git.py for git: this is the only live coverage of the driver.
"""

from __future__ import annotations

import pytest

from harness.drivers.memory import MemoryCommandRunner
from harness.drivers.subprocess_command import SubprocessCommandRunner
from harness.ports.command import CommandResult, CommandTimeout


async def test_zero_exit_and_output(tmp_path):
    runner = SubprocessCommandRunner()
    result = await runner.run("echo hello", cwd=tmp_path, timeout=10.0)
    assert result.exit_code == 0
    assert "hello" in result.output


async def test_nonzero_exit_with_merged_stderr(tmp_path):
    runner = SubprocessCommandRunner()
    result = await runner.run("echo oops >&2; exit 3", cwd=tmp_path, timeout=10.0)
    assert result.exit_code == 3
    assert "oops" in result.output


async def test_runs_in_cwd(tmp_path):
    (tmp_path / "marker.txt").write_text("here")
    runner = SubprocessCommandRunner()
    result = await runner.run("ls", cwd=tmp_path, timeout=10.0)
    assert "marker.txt" in result.output


async def test_timeout_kills_and_raises(tmp_path):
    runner = SubprocessCommandRunner()
    with pytest.raises(CommandTimeout):
        await runner.run("sleep 5", cwd=tmp_path, timeout=0.2)


async def test_memory_runner_scripts_results(tmp_path):
    runner = MemoryCommandRunner(
        results=[CommandResult(1, "boom"), CommandResult(0, "fine")]
    )
    first = await runner.run("make test", cwd=tmp_path, timeout=5.0)
    second = await runner.run("make test", cwd=tmp_path, timeout=5.0)
    third = await runner.run("make test", cwd=tmp_path, timeout=5.0)
    assert (first.exit_code, second.exit_code, third.exit_code) == (1, 0, 0)
    assert [call["command"] for call in runner.calls] == ["make test"] * 3
    assert runner.calls[0]["cwd"] == tmp_path
