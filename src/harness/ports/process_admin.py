"""ProcessAdmin — the write-side counterpart of the process repository, for the
admin UI.

Like `AgentAdmin` sits beside `AgentCatalog` and `WorkflowAdmin` beside
`WorkflowRepository`, `ProcessAdmin` sits beside `FilesystemProcessRepository`:
the runtime reads processes by compiling them into `ScheduledTrigger`s at
startup, an operator edits them through `ProcessAdmin`. Both know only
`models`-adjacent data — never a driver.

Unlike a workflow (kept as raw text) a Process is a small structured aggregate
(trigger × action × target × sink), so the editable shape is a typed
`ProcessFields`, mirroring `AgentFields` — a structured form, not a JSON blob.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProcessFields:
    """The structured shape a Process form submits and reads back.

    `name` is supplied separately to `ProcessAdmin.write`, never taken from here,
    so a submitted body can never smuggle in a different name than the URL path.
    `params` is the check's parameter dict (already parsed from the form's JSON
    textarea). Exactly one of the target roles is meaningful: `target_kind`
    selects whether `target` names a workflow or a step.

    `cadence` mirrors that same discriminator pattern for the schedule:
    `"interval"` or `"cron"` selects which of `interval`/`cron` is the
    authoritative value. It is an explicit field rather than inferred from
    "whichever is non-blank" so a submission that toggles to cron but leaves
    the box empty is reported against `errors.cron`, not `errors.interval`.
    """

    check: str
    target_kind: str  # "workflow" | "step"
    target: str
    cadence: str = "interval"  # "interval" | "cron"
    interval: str = ""
    cron: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    sink_kind: str = "none"
    dedup: str = "per-interval"


class ProcessNotFound(Exception):
    """Raised by `read` when no process by that name exists (or its file is
    unreadable). Mirrors `AgentNotFound`/`WorkflowNotFound`."""


class ProcessAdminValidationError(Exception):
    """Field name -> human-readable message. Raised by `write`; a rejected
    submission never leaves a partially written file behind. Named distinctly
    from the driver-level `ProcessValidationError` (which carries a single
    file-naming message) so the two never collide."""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{key}: {value}" for key, value in errors.items()))


class ProcessAdmin(ABC):
    """Read/write access to the process definition files, for the admin UI."""

    @abstractmethod
    def list(self) -> tuple[str, ...]:
        """Every process name currently defined, sorted."""

    @abstractmethod
    def read(self, name: str) -> ProcessFields:
        """Raises ProcessNotFound when unknown. Returns the editable fields."""

    @abstractmethod
    def write(self, name: str, fields: ProcessFields) -> ProcessFields:
        """Validates `fields` the same way `FilesystemProcessRepository` compiles
        a file, writes `<name>.json` only on success, and returns the resulting
        fields. Raises ProcessAdminValidationError — never partially writes."""

    @abstractmethod
    def delete(self, name: str) -> bool:
        """True if a process by that name existed and was removed."""

    @abstractmethod
    def check_names(self) -> tuple[str, ...]:
        """The action (`Check`) kinds the form offers as options, sorted. The
        driver returns the built-in registry's names; `api/` reads them through
        this port so the UI never imports a driver (invariant #5)."""

    @abstractmethod
    def sink_kinds(self) -> tuple[str, ...]:
        """The sink kinds the form offers, sorted: `("github", "none",
        "slack")` — the outbound destinations a Process may declare (invariant
        #40). A new destination is a new kind plus a sink driver, surfaced
        here."""
