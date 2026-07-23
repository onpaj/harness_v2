"""The `MergeChecker` port: reports whether a task's PR has been merged.

A read capability, deliberately separate from `Forge` — `Forge` only *opens*
PRs ("the merge strategy is a human's call"); `MergeChecker` only *reads* their
state back. Mirrors the read/write split already established between
`BoardView` and `TaskControl`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Task


class MergeChecker(ABC):
    """Reports a task's PR merge state, read from `task.data["pr"]`."""

    @abstractmethod
    def is_merged(self, task: Task) -> bool | None:
        """True once merged, False while open, None if the task carries no
        `data.pr`.

        A transient failure (network, API) must raise — the caller needs to
        tell "not merged" apart from "couldn't check" so it can retry instead
        of never archiving a merged task.
        """
