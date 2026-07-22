"""AgentAdmin — the write-side counterpart of `AgentCatalog`, for the admin UI.

Like `TaskControl` sits beside `BoardView`, `AgentAdmin` sits beside
`AgentCatalog`: the runtime reads personas through `AgentCatalog`, an operator
edits them through `AgentAdmin`. Both ports know only `models`/`ports.agent` —
never a driver.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from harness.ports.agent import AgentSpec


@dataclass(frozen=True)
class AgentFields:
    """The raw, unvalidated shape a form/JSON body submits. `name` is supplied
    separately to `AgentAdmin.write`, never taken from here, so a submitted
    body can never smuggle in a different name than the URL path names."""

    prompt: str
    model: str | None = None
    fallback_model: str | None = None
    allowed_tools: tuple[str, ...] = ()
    allowed_outcomes: tuple[str, ...] = ()  # raw strings; AgentAdmin parses Outcome(...)


class AgentValidationError(Exception):
    """Field name -> human-readable message. Raised by `write`; a rejected
    submission never leaves a partially written file behind."""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{key}: {value}" for key, value in errors.items()))


class AgentAdmin(ABC):
    """Read/write access to the agent catalog's files, for the admin UI."""

    @abstractmethod
    def list(self) -> tuple[str, ...]:
        """Every agent name currently defined, sorted."""

    @abstractmethod
    def read(self, name: str) -> AgentSpec:
        """Raises AgentNotFound (reused from ports/agent.py) when unknown."""

    @abstractmethod
    def write(self, name: str, fields: AgentFields) -> AgentSpec:
        """Validates `fields` the same way the runtime catalog would parse a
        file, writes `<name>.json` only on success, and returns the resulting
        AgentSpec. Raises AgentValidationError — never partially writes."""

    @abstractmethod
    def delete(self, name: str) -> bool:
        """True if an agent by that name existed and was removed."""
