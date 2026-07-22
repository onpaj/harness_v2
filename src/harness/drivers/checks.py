"""Built-in `Check` implementations and the name → factory registry.

A trigger names a check kind (`"always"`, `"disk-threshold"`) and hands it a
params dict; `BUILTIN_CHECKS` maps that name to a `CheckFactory` that builds the
concrete `Check`. `AlwaysCheck` fires every interval unconditionally;
`DiskThresholdCheck` fires while a filesystem sits at or above a percentage full.
"""

from __future__ import annotations

import shutil
from typing import Any, Callable

from harness.ports.triggers import Check, CheckFactory, Observation


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


BUILTIN_CHECKS: dict[str, CheckFactory] = {
    "always": lambda params: AlwaysCheck(),
    "disk-threshold": lambda params: DiskThresholdCheck(
        path=params["path"], percent=params["percent"]
    ),
}
