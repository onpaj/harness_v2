"""Agent port — persona catalog, spec, and how they run.

The behavior driver hands a step's work to an **agent**. Who the agent is is
pure data: `AgentSpec` describes the persona (prompt, model, allowed tools and
outcomes). `AgentCatalog` maps a step name to a spec, `AgentRunner` runs the
spec. The runner knows nothing about queues or routing — it gets a prompt and
returns a verdict.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from harness.models import Outcome


@dataclass(frozen=True)
class AgentSpec:
    """Persona as data. `allowed_outcomes` bounds what the verdict may return."""

    name: str
    prompt: str
    model: str | None = None
    fallback_model: str | None = None
    allowed_tools: tuple[str, ...] = ()
    allowed_outcomes: tuple[Outcome, ...] = (Outcome.DONE,)
    timeout: float | None = None


@dataclass(frozen=True)
class AgentRun:
    """Result of a single agent run: verdict, summary, and raw output."""

    outcome: Outcome
    summary: str
    raw: str = ""


class AgentRunner(ABC):
    """Runs a spec with the given prompt in `cwd` and returns a verdict."""

    @abstractmethod
    async def run(
        self,
        *,
        prompt: str,
        spec: AgentSpec,
        cwd: Path,
        timeout: float,
        on_output: Callable[[str], None] | None = None,
    ) -> AgentRun:
        """Run the agent and return its verdict. Failures propagate as exceptions.

        `on_output`, when given, is called with each rendered activity line as the
        agent works — the seam through which the harness streams live stage output.
        A runner that can't stream simply ignores it.
        """


class AgentCatalog(ABC):
    """Maps a name (typically a step) to an `AgentSpec`."""

    @abstractmethod
    def get(self, name: str) -> AgentSpec:
        """Return the spec for the name, or raise `AgentNotFound`."""

    @abstractmethod
    def names(self) -> tuple[str, ...]:
        """Every agent name discoverable without raising."""


class AgentNotFound(Exception):
    """The catalog has no agent by that name."""
