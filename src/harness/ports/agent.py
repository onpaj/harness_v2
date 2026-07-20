"""Port agenta — katalog person, spec a jejich běh.

Práci kroku svěří behavior driver **agentovi**. Kdo je agent, je čistě data:
`AgentSpec` popisuje personu (prompt, model, povolené nástroje a outcomes).
`AgentCatalog` mapuje jméno kroku na spec, `AgentRunner` spec spustí. Runner
nezná fronty ani rozhodování — dostane prompt a vrátí verdikt.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from harness.models import Outcome


@dataclass(frozen=True)
class AgentSpec:
    """Persona jako data. `allowed_outcomes` ohraničuje, co smí verdikt vrátit."""

    name: str
    prompt: str
    model: str | None = None
    fallback_model: str | None = None
    allowed_tools: tuple[str, ...] = ()
    allowed_outcomes: tuple[Outcome, ...] = (Outcome.DONE,)


@dataclass(frozen=True)
class AgentRun:
    """Výsledek jednoho běhu agenta: verdikt, shrnutí a syrový výstup."""

    outcome: Outcome
    summary: str
    raw: str = ""


class AgentRunner(ABC):
    """Spustí spec s daným promptem v `cwd` a vrátí verdikt."""

    @abstractmethod
    async def run(
        self, *, prompt: str, spec: AgentSpec, cwd: Path, timeout: float
    ) -> AgentRun:
        """Spusť agenta a vrať jeho verdikt. Selhání probublá výjimkou."""


class AgentCatalog(ABC):
    """Mapuje jméno (typicky krok) na `AgentSpec`."""

    @abstractmethod
    def get(self, name: str) -> AgentSpec:
        """Vrať spec pro jméno, nebo vyhoď `AgentNotFound`."""


class AgentNotFound(Exception):
    """Katalog nezná agenta daného jména."""
