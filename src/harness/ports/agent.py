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

from harness.models import DONE


@dataclass(frozen=True)
class AgentSpec:
    """Persona as data. `allowed_outcomes` bounds what the verdict may return."""

    name: str
    prompt: str
    model: str | None = None
    fallback_model: str | None = None
    allowed_tools: tuple[str, ...] = ()
    allowed_outcomes: tuple[str, ...] = (DONE,)
    timeout: float | None = None


@dataclass(frozen=True)
class AgentRun:
    """Result of a single agent run: verdict, summary, and raw output.

    `input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_creation_tokens`,
    `total_cost_usd` and `model` are best-effort telemetry lifted from the
    CLI's own usage reporting — record-only, never a correctness signal. A
    runner that can't produce them (a future non-Anthropic runner, or
    `FakeAgentRunner` when a test doesn't care) simply leaves the defaults.
    """

    outcome: str
    summary: str
    raw: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    total_cost_usd: float | None = None
    model: str | None = None
    """The resolved model that actually ran (post-`fallback_model`), when known."""


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
        A runner that can't stream simply ignores it. Token usage and the resolved
        model are best-effort telemetry — a runner that can't produce them leaves
        `AgentRun`'s defaults rather than raising.
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
