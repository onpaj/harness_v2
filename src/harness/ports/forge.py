"""Port forge — otevírání pull requestů.

Landing přes něj navrhne změnu. Harness se nikdy nedotkne cílové branch; jen
otevře PR. Merge strategii řeší člověk.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from harness.models import Task


@dataclass(frozen=True)
class PullRequest:
    number: int
    url: str
    branch: str
    title: str


class Forge(ABC):
    @abstractmethod
    def open_pull_request(
        self, task: Task, *, branch: str, title: str, body: str
    ) -> PullRequest:
        """Otevři PR pro branch. Idempotentní — existuje-li PR pro branch,
        vrať ho místo založení dalšího."""
