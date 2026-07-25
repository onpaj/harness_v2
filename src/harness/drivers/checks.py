"""Built-in `Check` implementations and the name → factory registry.

A trigger names a check kind (`"always"`, `"disk-threshold"`, `"fs-files"`,
`"command"`) and hands it a params dict; `BUILTIN_CHECKS` maps that name to a
`CheckFactory` that builds the concrete `Check`. `AlwaysCheck` fires every
interval unconditionally; `DiskThresholdCheck` fires while a filesystem sits at
or above a percentage full; `FileGlobCheck` fires per file matching a glob;
`CommandCheck` fires per non-empty stdout line of a shell command.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from harness.ports.triggers import (
    Check,
    CheckDefinition,
    CheckFactory,
    CheckSpec,
    Observation,
    ParamSpec,
)


class AlwaysCheck(Check):
    """Fire every interval unconditionally (one empty observation)."""

    def evaluate(self) -> list[Observation]:
        return [Observation()]


class DiskThresholdCheck(Check):
    """Fire while `path` is at or above `percent` full.

    `usage` is injectable for tests: a callable `path -> object` exposing
    `.total`/`.used`/`.free`, defaulting to `shutil.disk_usage`.
    """

    def __init__(
        self,
        *,
        path: str,
        percent: float,
        usage: Callable[[str], Any] = shutil.disk_usage,
    ) -> None:
        self._path = path
        self._percent = percent
        self._usage = usage

    def evaluate(self) -> list[Observation]:
        u = self._usage(self._path)
        if u.total == 0:
            return []
        if u.used / u.total * 100 >= self._percent:
            return [
                Observation(
                    state_key=f"{self._path}:over",
                    data={"title": f"disk {self._path} over {self._percent}%"},
                )
            ]
        return []


def _list_files(path: str, pattern: str) -> list[str]:
    """The default lister: files (not directories) under `path` matching
    `pattern`, sorted for determinism. A missing directory yields `[]`."""
    directory = Path(path)
    if not directory.is_dir():
        return []
    return sorted(str(p) for p in directory.glob(pattern) if p.is_file())


class FileGlobCheck(Check):
    """Fire once per file under `path` matching `pattern`.

    `lister` is injectable for tests: a callable `(path, pattern) -> list[str]`
    of file paths, defaulting to a real `pathlib` glob (files only, sorted).
    Each match is one `Observation` keyed by the file's path, so `per-state`
    dedup fires once per file. A missing directory yields `[]`, not an error.
    """

    def __init__(
        self,
        *,
        path: str,
        pattern: str = "*",
        lister: Callable[[str, str], list[str]] = _list_files,
    ) -> None:
        self._path = path
        self._pattern = pattern
        self._lister = lister

    def evaluate(self) -> list[Observation]:
        return [
            Observation(
                state_key=file,
                data={"title": f"file {file}", "file": file},
            )
            for file in self._lister(self._path, self._pattern)
        ]


class CommandCheck(Check):
    """Fire once per non-empty stdout line of a shell `command`.

    `runner` is injectable for tests: a callable `(command, timeout) ->
    CompletedProcess`, defaulting to `subprocess.run` (shell, captured text).
    Exit code 0 yields one `Observation` per non-empty stdout line, keyed by
    the stripped line; a non-zero exit or a timeout yields `[]`. Deliberately,
    `evaluate()` may block the poll tick briefly (the same posture as the
    sync-HTTP `GithubTaskSource.poll`), bounded by the modest default timeout.
    """

    def __init__(
        self,
        *,
        command: str,
        timeout: float = 30.0,
        runner: Callable[[str, float], subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._command = command
        self._timeout = timeout
        self._runner = runner if runner is not None else self._run

    @staticmethod
    def _run(command: str, timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )

    def evaluate(self) -> list[Observation]:
        try:
            completed = self._runner(self._command, self._timeout)
        except subprocess.TimeoutExpired:
            return []
        if completed.returncode != 0:
            return []
        return [
            Observation(state_key=line, data={"title": line})
            for raw in completed.stdout.splitlines()
            if (line := raw.strip())
        ]


# Each built-in is an action *definition* — a declarative `CheckSpec` (what the
# UI renders) bundled with the factory (what runs it). The spec is the single
# source of truth for the action's parameters; the form has nothing hardcoded.
BUILTIN_CHECKS: dict[str, CheckFactory] = {
    "always": CheckDefinition(
        spec=CheckSpec(
            name="always",
            label="Always",
            description="Fires every interval, unconditionally.",
        ),
        factory=lambda params: AlwaysCheck(),
    ),
    "disk-threshold": CheckDefinition(
        spec=CheckSpec(
            name="disk-threshold",
            label="Disk threshold",
            description="Fires while a disk is at or above a usage percentage.",
            params=(
                ParamSpec(
                    key="path", label="Path to watch", required=True, placeholder="/"
                ),
                ParamSpec(
                    key="percent",
                    label="Fire at usage (%)",
                    type="number",
                    required=True,
                    placeholder="90",
                ),
            ),
        ),
        factory=lambda params: DiskThresholdCheck(
            path=params["path"], percent=params["percent"]
        ),
    ),
    "fs-files": CheckDefinition(
        spec=CheckSpec(
            name="fs-files",
            label="Files in a folder",
            description="Fires once per file matching a glob pattern.",
            params=(
                ParamSpec(
                    key="path", label="Directory", required=True, placeholder="/var/drop"
                ),
                ParamSpec(key="pattern", label="Glob pattern", placeholder="*"),
            ),
        ),
        factory=lambda params: FileGlobCheck(
            path=params["path"], pattern=params.get("pattern", "*")
        ),
    ),
    "command": CheckDefinition(
        spec=CheckSpec(
            name="command",
            label="Shell command",
            description="Runs a command; every non-empty output line becomes a task.",
            params=(
                ParamSpec(
                    key="command",
                    label="Command",
                    required=True,
                    placeholder="e.g. find /drop -name '*.job'",
                    hint="Runs in a shell; each non-empty stdout line becomes one task.",
                ),
                ParamSpec(
                    key="timeout",
                    label="Timeout (seconds)",
                    type="number",
                    placeholder="30",
                ),
            ),
        ),
        factory=lambda params: CommandCheck(
            command=params["command"], timeout=params.get("timeout", 30.0)
        ),
    ),
}
