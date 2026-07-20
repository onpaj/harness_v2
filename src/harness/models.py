"""Datové modely. Tento modul neimportuje nic z balíku harness."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Union

END = "end"
"""Vyhrazené jméno terminálního uzlu. Nemá frontu ani odchozí hrany."""

FAILED = "failed"
"""Vyhrazený terminální status tasku, který skončil ve frontě `failed/`.
Stejně jako END nemá žádné odchozí hrany — jen tu navíc žádný workflow
nezná jako svůj krok."""


class Outcome(str, Enum):
    """Jediné hodnoty, které smí ConsumerBehavior vrátit."""

    DONE = "done"
    REQUEST_CHANGES = "request_changes"


@dataclass(frozen=True)
class BehaviorResult:
    """Návrat behavioru: co se stalo (outcome) a co se udělalo (summary).

    `outcome` je řídicí signál, na který routuje dispatcher. `summary` je krátký
    terminální výrok o běhu — zpráva commitu, řádek historie, tělo PR, board.
    """

    outcome: Outcome
    summary: str = ""


@dataclass(frozen=True)
class HistoryEntry:
    """Jeden řádek audit logu tasku."""

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
    """Jednotka práce. Putuje mezi frontami, nese svá metadata."""

    id: str
    workflow_template: str
    created: str
    repository: str | None = None
    worktree: str | None = None
    status: str | None = None
    last_outcome: str | None = None
    lock_id: str | None = None
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
            history=tuple(
                HistoryEntry.from_dict(entry) for entry in raw.get("history", [])
            ),
            data=raw.get("data") or {},
        )


@dataclass(frozen=True)
class Transition:
    """Jedna hrana state machine: z kroku, na outcome, do kroku."""

    from_step: str
    on: str
    to_step: str


@dataclass(frozen=True)
class Workflow:
    name: str
    start: str
    transitions: tuple[Transition, ...]

    def target(self, status: str, outcome: str) -> str | None:
        """Cíl hrany, nebo None když žádná nesedí."""
        for transition in self.transitions:
            if transition.from_step == status and transition.on == outcome:
                return transition.to_step
        return None

    def steps(self) -> tuple[str, ...]:
        """Všechny kroky, které potřebují frontu. END mezi ně nepatří."""
        found: list[str] = []
        for transition in self.transitions:
            for step in (transition.from_step, transition.to_step):
                if step != END and step not in found:
                    found.append(step)
        if self.start != END and self.start not in found:
            found.append(self.start)
        return tuple(found)


@dataclass(frozen=True)
class MoveTo:
    """Task jde do fronty kroku."""

    step: str


@dataclass(frozen=True)
class Finished:
    """Task doputoval na END."""


@dataclass(frozen=True)
class Failed:
    """Task nelze směrovat."""

    reason: str


Decision = Union[MoveTo, Finished, Failed]


def append_history(task: Task, entry: HistoryEntry) -> Task:
    return replace(task, history=task.history + (entry,))
