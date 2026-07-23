from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Task


class TaskQueue(ABC):
    """Task queue.

    The inbox, the per-step queues, done and failed are all instances of this
    port. Terminal states are simply queues that nobody consumes.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def list(self) -> list[Task]:
        """Unclaimed tasks in the queue."""

    @abstractmethod
    def claim(self, task: Task, lock_id: str) -> Task | None:
        """Claim a task. Returns the task with lockId set, or None on a lost race."""

    @abstractmethod
    def put(self, task: Task) -> None:
        """Insert an unclaimed task."""

    @abstractmethod
    def transfer(self, task: Task, destination: TaskQueue) -> None:
        """Move a claimed task to another queue.

        Must be atomic: the task must never exist in both queues at once.
        """

    @abstractmethod
    def recover(self) -> int:
        """Return claimed tasks to the queue and clear lockId. Returns the count."""

    @abstractmethod
    def discard(self, task: Task) -> None:
        """Permanently remove a claimed task — the terminal counterpart to
        transfer(), which moves a claimed task elsewhere instead. The task
        must currently be held (i.e. returned by a prior claim())."""
