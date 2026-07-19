from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Task


class TaskQueue(ABC):
    """Fronta tasků.

    Inbox, fronty jednotlivých kroků, done i failed jsou instance tohoto
    portu. Terminální stavy jsou prostě fronty, které nikdo nekonzumuje.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def list(self) -> list[Task]:
        """Nezabrané tasky ve frontě."""

    @abstractmethod
    def claim(self, task: Task, lock_id: str) -> Task | None:
        """Zaber task. Vrátí task s vyplněným lockId, nebo None při prohraném závodě."""

    @abstractmethod
    def put(self, task: Task) -> None:
        """Vlož nezabraný task."""

    @abstractmethod
    def transfer(self, task: Task, destination: TaskQueue) -> None:
        """Přesuň zabraný task do jiné fronty.

        Musí být atomické: task nesmí existovat v obou frontách zároveň.
        """

    @abstractmethod
    def recover(self) -> int:
        """Vrať zabrané tasky zpět do fronty a vynuluj lockId. Vrací počet."""
