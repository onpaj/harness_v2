"""Port úložiště artefaktů.

Harnessem vlastněná složka, kam fáze píší artefakty (plán, design, review).
Neverzovaná během tasku, čitelná pro UI. Artefakty jsou attempt-indexed —
re-run kroku nikdy nepřepíše předchozí pokus.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ArtifactRef:
    """Adresa jednoho artefaktu: krok, pokus, jméno."""

    step: str
    attempt: int
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step, "attempt": self.attempt, "name": self.name}


class ArtifactSlot(ABC):
    """Jeden pokus daného kroku. Vše zapsané sem patří k témuž běhu."""

    @property
    @abstractmethod
    def attempt(self) -> int:
        """Pořadové číslo pokusu (0, 1, 2, …)."""

    @abstractmethod
    def put(self, name: str, content: str) -> None:
        """Zapiš artefakt do tohoto pokusu."""


class ArtifactView(ABC):
    """Read-only pohled na artefakty. Jediné, co o úložišti ví UI."""

    @abstractmethod
    def list(self, task_id: str) -> tuple[ArtifactRef, ...]:
        """Všechny artefakty tasku napříč kroky a pokusy."""

    @abstractmethod
    def read(self, task_id: str, step: str, attempt: int, name: str) -> str | None:
        """Obsah artefaktu, nebo None když neexistuje."""


class ArtifactStore(ArtifactView):
    """Zápisová strana úložiště."""

    @abstractmethod
    def begin(self, task_id: str, step: str) -> ArtifactSlot:
        """Alokuj další pokus pro dvojici (task, step) a vrať slot."""
