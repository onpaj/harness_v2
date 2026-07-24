"""The CommandRunner port — a shell command run to completion in a directory.

The verify step's counterpart of `AgentRunner`: the behavior decides *what* to
run and how to map the result; only the driver knows subprocesses. Timeouts
raise (the process is killed) — a behavior must treat a timeout as a step
failure, never as a red suite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    """What the command did: exit code plus merged stdout+stderr."""

    exit_code: int
    output: str


class CommandTimeout(Exception):
    """The command exceeded its time budget and was killed."""


class CommandRunner(ABC):
    @abstractmethod
    async def run(self, command: str, *, cwd: Path, timeout: float) -> CommandResult:
        """Run to completion in `cwd`. Raises CommandTimeout past `timeout`."""
