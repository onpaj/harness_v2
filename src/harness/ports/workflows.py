from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Workflow


class WorkflowNotFound(Exception):
    """No workflow by that name exists."""


class WorkflowRepository(ABC):
    @abstractmethod
    def get(self, name: str) -> Workflow:
        """Load a workflow. If it does not exist, raise WorkflowNotFound."""

    @abstractmethod
    def names(self) -> tuple[str, ...]:
        """Every workflow name discoverable without raising. Best-effort: a
        name whose file fails to parse is simply absent from the result."""
