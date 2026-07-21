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
        """All workflow names discoverable here.

        Lenient on enumeration: an unreadable or invalid definition is simply
        skipped here, not raised — it still fails loud from get() if actually
        requested.
        """
