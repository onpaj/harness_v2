"""Port pracovní plochy — worktree, ve kterém fáze upravují kód.

`attach` připojí task k worktree pojmenovanému v tasku (`repository`/`worktree`).
Handle umí zapsat soubor a commitnout. Commit dělá behavior driver, nikdy
consumer ani LLM.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from harness.models import Task


class WorkspaceHandle(ABC):
    """Připojený worktree tasku."""

    @property
    @abstractmethod
    def path(self) -> Path:
        """Pracovní adresář."""

    @property
    @abstractmethod
    def branch(self) -> str:
        """Task branch, na které commity leží."""

    @abstractmethod
    def write(self, relpath: str, content: str) -> None:
        """Zapiš soubor relativně k worktree.

        Používá se i landingem k přiklopení artefaktů — proto je to metoda
        handle, ne přímý zápis přes `path`: memory driver ji zaznamená, takže
        landing jde testovat bez disku.
        """

    @abstractmethod
    def commit(self, message: str) -> str | None:
        """Stageuj vše a commitni. Vrať sha, nebo None když není co commitovat."""


class Workspace(ABC):
    @abstractmethod
    def attach(self, task: Task) -> WorkspaceHandle:
        """Připoj task k jeho worktree. Neexistuje-li, založ ho na task branchi."""
