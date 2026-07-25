"""Real CommandRunner over asyncio's subprocess shell."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

from harness.ports.command import CommandResult, CommandRunner, CommandTimeout


class SubprocessCommandRunner(CommandRunner):
    async def run(self, command: str, *, cwd: Path, timeout: float) -> CommandResult:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            raise CommandTimeout(
                f"command exceeded {timeout:.0f}s: {command}"
            ) from None
        return CommandResult(
            exit_code=process.returncode,
            output=stdout.decode("utf-8", errors="replace"),
        )
