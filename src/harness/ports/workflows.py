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
    def names(self) -> list[str]:
        """All discoverable workflow names, sorted alphabetically.

        Lenient where get() is strict: a definition that fails to parse is
        omitted, never raised. A missing/unreadable root yields an empty list.
        """
