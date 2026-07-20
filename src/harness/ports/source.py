"""The `TaskSource` port: the outer world of work management behind one interface.

Tasks no longer arise only by hand (`harness submit`). They flow in from the
real world (GitHub Issues, a drop folder, Jira) and their state is projected
back out. That whole world lives behind three verbs:

- `poll()` — bring in new, not-yet-consumed tasks.
- `report_progress(task, progress)` — project the in-progress state outward.
- `finish(task, result)` — project the terminal state (success / failure).

GitHub is one implementation, the filesystem another — swapping the driver,
never its surroundings. The harness's internal vocabulary
`(status, last_outcome, queue)` doesn't leak through the port: the reflector
maps it to `Progress`/`FinishResult`, and the adapter maps that to a label.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from harness.models import Task


def source_key(task: Task) -> tuple[Any, ...] | None:
    """The task's unique identity in its source, or None if it has none.

    A task's origin lives in `task.data.source` (invariant #19). Two tasks with
    the same `(kind, repo, issue)` are the *same* piece of outside work — a
    GitHub issue must never yield two tasks, no matter how often it is polled or
    how many times the harness restarts. Hand-submitted tasks (`harness submit`)
    carry no `source` and are intentionally never deduplicated: each submit is a
    fresh unit of work.
    """
    src = task.data.get("source")
    if not isinstance(src, dict):
        return None
    kind = src.get("kind")
    issue = src.get("issue")
    if kind is None or issue is None:
        return None
    return (kind, src.get("repo"), issue)


@dataclass(frozen=True)
class Progress:
    """A task's in-progress state as the outer world sees it (no harness knowledge)."""

    step: str            # the step the task has just entered
    summary: str = ""    # what happened (optional, from history)


@dataclass(frozen=True)
class FinishResult:
    """A task's terminal state, projected outward."""

    ok: bool
    pr_url: str | None = None
    summary: str = ""


class TaskSource(ABC):
    """A source of tasks and a target for projecting their state.

    `kind` is the key for projection routing: the reflector calls only the
    adapter whose `kind` matches `task.data.source.kind`. A foreign task the
    adapter silently ignores.
    """

    kind: str

    @abstractmethod
    def poll(self) -> list[Task]:
        """Bring in new, not-yet-consumed tasks."""

    @abstractmethod
    def report_progress(self, task: Task, progress: Progress) -> None:
        """Project the in-progress state outward. No-op for a foreign task."""

    @abstractmethod
    def finish(self, task: Task, result: FinishResult) -> None:
        """Project the terminal state outward. No-op for a foreign task."""
