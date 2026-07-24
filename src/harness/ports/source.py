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

from harness.models import Task


def effective_sink_kind(task: Task) -> str | None:
    """The destination a sink routes on: `data.sink.kind` if the task carries
    an explicit sink, else `data.source.kind` (the degenerate case where the
    destination coincides with the origin). `None` when neither is present.

    Every reflecting `TaskSource` (`GithubLabelReflector`, `SlackWebhookSink`)
    compares this against its own `kind` in `_mine` — one routing rule for all
    outbound reflection, invariant #40.
    """
    sink = task.data.get("sink")
    if isinstance(sink, dict) and sink.get("kind"):
        return sink["kind"]
    return task.data.get("source", {}).get("kind")


def dedup_key(kind: str, *parts: object) -> str:
    """The stable identity of a source item, as stamped onto `Task.dedup_key`.

    A `TaskSource` builds it from whatever uniquely names the item in its world
    — for GitHub that is `(repo, issue)`. Two items with the same key are the
    *same* piece of outside work: a GitHub issue must never yield two tasks, no
    matter how often it is polled or how many times the harness restarts. The
    poller compares this key across every task already on disk, so the guarantee
    is persistent (see `SourcePoller`).
    """
    return ":".join([kind, *(str(part) for part in parts)])


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
    adapter whose `kind` matches the task's *effective sink kind*
    (`effective_sink_kind` — `data.sink.kind`, falling back to
    `data.source.kind`). A foreign task the adapter silently ignores.
    """

    kind: str

    @abstractmethod
    def poll(self) -> list[Task]:
        """Bring in new, not-yet-consumed tasks.

        An implementation may also perform an idempotent, side-effecting
        action per polled item that produces no task (precedent:
        `GithubTaskSource.poll()` swaps a label as part of claiming an issue;
        `GithubMergeabilityWatcher.poll()` calls GitHub's update-branch API on
        a "behind" PR). Any such action must be safe to repeat every tick.
        """

    @abstractmethod
    def report_progress(self, task: Task, progress: Progress) -> None:
        """Project the in-progress state outward. No-op for a foreign task."""

    @abstractmethod
    def finish(self, task: Task, result: FinishResult) -> None:
        """Project the terminal state outward. No-op for a foreign task."""


class Trigger(TaskSource):
    """A `TaskSource` that produces tasks but reflects nothing back outward.

    A schedule- or condition-trigger has an inbound side only: it implements
    `poll()` and nothing else. `report_progress`/`finish` are concrete no-ops
    here, so a subclass supplying just `poll()` becomes fully concrete (`poll`
    stays abstract, so `Trigger` itself cannot be instantiated).

    It is still a `TaskSource`, wired the same way: `SourcePoller` ingests it,
    and `SourceReflectorSink` lists it among the sinks and simply skips it — a
    `Trigger` stamps no matching `data.source`, so no projection is ever routed
    back to it.
    """

    def report_progress(self, task: Task, progress: Progress) -> None:
        """A trigger reflects nothing outward: a no-op."""
        return None

    def finish(self, task: Task, result: FinishResult) -> None:
        """A trigger reflects nothing outward: a no-op."""
        return None
