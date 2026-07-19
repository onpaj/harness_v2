"""The queue interface the dispatcher programs against."""

from __future__ import annotations

from abc import ABC, abstractmethod

from agentharness.models import Task


class Queue(ABC):
    """A durable, at-least-once work queue partitioned by agent."""

    @abstractmethod
    def enqueue(self, task: Task) -> bool:
        """Add a task. Returns False when its idempotency_key was already seen."""

    @abstractmethod
    def lease(self, agent: str, visibility_timeout: int) -> Task | None:
        """Take the highest-priority ready task, hiding it for `visibility_timeout` seconds."""

    @abstractmethod
    def ack(self, task: Task) -> None:
        """Mark a leased task as completed and drop it."""

    @abstractmethod
    def nack(self, task: Task, *, requeue: bool = True, delay_seconds: float = 0.0) -> None:
        """Release a leased task, optionally returning it to the queue after a delay."""

    @abstractmethod
    def dead_letter(self, task: Task, reason: str) -> None:
        """Move a leased task to the dead-letter store along with why it got there."""

    @abstractmethod
    def depth(self, agent: str) -> int:
        """Number of tasks currently pending for `agent`."""

    @abstractmethod
    def reclaim_expired(self, now: float | None = None) -> list[Task]:
        """Return leases whose visibility timeout has passed to the pending queue."""

    @abstractmethod
    def list_dead(self, agent: str) -> list[Task]:
        """Every dead-lettered task for `agent`."""

    @abstractmethod
    def agents_with_work(self) -> list[str]:
        """Agents that have at least one pending or ready-delayed task."""
