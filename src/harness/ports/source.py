"""Port `TaskSource`: vnější svět řízení práce za jedním rozhraním.

Task přestává vznikat jen ručně (`harness submit`). Přitéká z reálného světa
(GitHub Issues, drop-folder, Jira) a jeho stav se promítá zpět. Celý ten svět
leží za třemi slovesy:

- `poll()` — přines nové, ještě nezkonzumované tasky.
- `report_progress(task, progress)` — promítni průběžný stav ven.
- `finish(task, result)` — promítni terminální stav (úspěch / selhání).

GitHub je jedna implementace, filesystem druhá — záměna driveru, nikdy jeho
okolí. Interní slovník harnessu `(status, last_outcome, queue)` portem
neprosakuje: reflector ho zmapuje na `Progress`/`FinishResult`, adapter ten na
label.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from harness.models import Task


@dataclass(frozen=True)
class Progress:
    """Průběžný stav tasku, jak ho vidí vnější svět (bez znalosti harnessu)."""

    step: str            # krok, do kterého task právě vstoupil
    summary: str = ""    # co se stalo (volitelné, z historie)


@dataclass(frozen=True)
class FinishResult:
    """Terminální stav tasku promítaný ven."""

    ok: bool
    pr_url: str | None = None
    summary: str = ""


class TaskSource(ABC):
    """Zdroj tasků a cíl projekce jejich stavu.

    `kind` je klíč pro routing projekce: reflector volá jen ten adapter, jehož
    `kind` sedí na `task.data.source.kind`. Cizí task adapter tiše ignoruje.
    """

    kind: str

    @abstractmethod
    def poll(self) -> list[Task]:
        """Přines nové, ještě nezkonzumované tasky."""

    @abstractmethod
    def report_progress(self, task: Task, progress: Progress) -> None:
        """Promítni průběžný stav ven. No-op pro cizí task."""

    @abstractmethod
    def finish(self, task: Task, result: FinishResult) -> None:
        """Promítni terminální stav ven. No-op pro cizí task."""
