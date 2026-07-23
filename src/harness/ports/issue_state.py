"""The `IssueChecker` port: reports whether a task's originating issue is open.

A read capability on the *source* side, deliberately separate from `TaskSource`
(whose three verbs — `poll`/`report_progress`/`finish` — are fixed, invariant
18) and from `IssueTracker` (which *opens* issues for the healer). This one only
*reads back* the state of the issue a task was born from, so the harness can
retire a task whose issue was closed or deleted out from under it.

Mirrors `MergeChecker` exactly, only it asks about the source issue instead of
the landed PR: `MergeChecker` pulls the *outcome* of finished work back in,
`IssueChecker` pulls the *disappearance of the reason* for the work back in.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Task


class IssueChecker(ABC):
    """Reports whether the issue a task originated from is still open."""

    @abstractmethod
    def is_open(self, task: Task) -> bool | None:
        """True while the source issue is open, False once it is closed or gone,
        None when the task carries no issue this checker can resolve (no
        `data.source`, or a `kind` this checker doesn't handle).

        A transient failure (network, API) must raise — the caller needs to tell
        "closed" apart from "couldn't check" so it retries instead of archiving a
        task whose issue is merely unreachable this tick.
        """
