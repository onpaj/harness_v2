from __future__ import annotations

from abc import ABC, abstractmethod

from harness.models import Workflow


class WorkflowNotFound(Exception):
    """Workflow daného jména neexistuje."""


class WorkflowRepository(ABC):
    @abstractmethod
    def get(self, name: str) -> Workflow:
        """Načti workflow. Neexistuje-li, vyhoď WorkflowNotFound."""
