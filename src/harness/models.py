"""Data models. This module imports nothing from the harness package."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Union

END = "end"
"""Reserved name of the terminal node. It has no queue and no outgoing edges."""

FAILED = "failed"
"""Reserved terminal status of a task that ended up in the `failed/` queue.
Like END it has no outgoing edges — it just additionally isn't known to any
workflow as one of its steps. Unlike a true terminal, `failed/` has exactly one
reader: the `Healer` loop, which drains it into `healed/` (invariant 24)."""

HEALED = "healed"
"""Reserved terminal status of a task the healer has settled onto the `healed/`
queue. This is the never-consumed terminal that `failed/` used to be — the
healer reads `failed/` and moves a task here once, success or failure, so a
failure can never be healed twice (invariant 25)."""


class Outcome(str, Enum):
    """The only values a ConsumerBehavior may return."""

    DONE = "done"
    REQUEST_CHANGES = "request_changes"


@dataclass(frozen=True)
class BehaviorResult:
    """A behavior's return value: what happened (outcome) and what was done (summary).

    `outcome` is the control signal the dispatcher routes on. `summary` is a short
    terminal statement about the run — commit message, history line, PR body, board.
    """

    outcome: Outcome
    summary: str = ""


@dataclass(frozen=True)
class HistoryEntry:
    """A single line of the task's audit log."""

    at: str
    actor: str
    from_step: str | None
    to_step: str | None
    outcome: str | None = None
    summary: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        raw: dict[str, Any] = {
            "at": self.at,
            "actor": self.actor,
            "from": self.from_step,
            "to": self.to_step,
        }
        if self.outcome is not None:
            raw["outcome"] = self.outcome
        if self.summary is not None:
            raw["summary"] = self.summary
        if self.reason is not None:
            raw["reason"] = self.reason
        return raw

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> HistoryEntry:
        return cls(
            at=raw["at"],
            actor=raw["actor"],
            from_step=raw.get("from"),
            to_step=raw.get("to"),
            outcome=raw.get("outcome"),
            summary=raw.get("summary"),
            reason=raw.get("reason"),
        )


@dataclass(frozen=True)
class Task:
    """Unit of work. Travels between queues, carries its own metadata.

    `dedup_key` is the task's stable identity in the outside world that produced
    it (e.g. a GitHub issue). It is stamped once by the `TaskSource` at creation
    and persisted with the task, so a source item ingested once is never
    ingested again — the guarantee survives a harness restart because the key
    lives on the task on disk, not in a transient in-process ledger. A task with
    no external origin (`harness submit`) has none: each submit is fresh work.
    """

    id: str
    workflow_template: str
    created: str
    repository: str | None = None
    worktree: str | None = None
    status: str | None = None
    last_outcome: str | None = None
    lock_id: str | None = None
    dedup_key: str | None = None
    history: tuple[HistoryEntry, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "repository": self.repository,
            "worktree": self.worktree,
            "workflowTemplate": self.workflow_template,
            "status": self.status,
            "lastOutcome": self.last_outcome,
            "lockId": self.lock_id,
            "dedupKey": self.dedup_key,
            "created": self.created,
            "history": [entry.to_dict() for entry in self.history],
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Task:
        return cls(
            id=raw["id"],
            workflow_template=raw["workflowTemplate"],
            created=raw["created"],
            repository=raw.get("repository"),
            worktree=raw.get("worktree"),
            status=raw.get("status"),
            last_outcome=raw.get("lastOutcome"),
            lock_id=raw.get("lockId"),
            dedup_key=raw.get("dedupKey"),
            history=tuple(
                HistoryEntry.from_dict(entry) for entry in raw.get("history", [])
            ),
            data=raw.get("data") or {},
        )


@dataclass(frozen=True)
class Transition:
    """A single state-machine edge: from step, on outcome, to step."""

    from_step: str
    on: str
    to_step: str


@dataclass(frozen=True)
class Workflow:
    name: str
    start: str
    transitions: tuple[Transition, ...]
    max_parallel: dict[str, int] = field(default_factory=dict)

    def target(self, status: str, outcome: str) -> str | None:
        """Target of the matching edge, or None when none matches."""
        for transition in self.transitions:
            if transition.from_step == status and transition.on == outcome:
                return transition.to_step
        return None

    def steps(self) -> tuple[str, ...]:
        """All steps that need a queue. END is not one of them."""
        found: list[str] = []
        for transition in self.transitions:
            for step in (transition.from_step, transition.to_step):
                if step != END and step not in found:
                    found.append(step)
        if self.start != END and self.start not in found:
            found.append(self.start)
        return tuple(found)

    def max_parallel_for(self, step: str) -> int:
        """The configured concurrency limit for a step. Absent entries default to 1."""
        return self.max_parallel.get(step, 1)


@dataclass(frozen=True)
class MoveTo:
    """The task goes to a step's queue."""

    step: str


@dataclass(frozen=True)
class Finished:
    """The task has reached END."""


@dataclass(frozen=True)
class Failed:
    """The task cannot be routed."""

    reason: str


Decision = Union[MoveTo, Finished, Failed]


def append_history(task: Task, entry: HistoryEntry) -> Task:
    return replace(task, history=task.history + (entry,))
