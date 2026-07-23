"""Updater — the UI-facing port for the in-place self-upgrade.

The board can already *show* the running version (`GET /api/version`); this port
lets it *change* it: run `uv tool upgrade harness` and, when the version moved,
restart the service. It is the write-side counterpart of the version string the
footer renders, exactly as `TaskControl` is the write-side counterpart of
`BoardView`.

Kept behind a port because the upgrade is raw substrate work — a `uv` subprocess
and a `launchctl` restart — and the UI must know nothing about what the harness
runs on (invariant 5). `api/` reaches only for `Updater`; the driver
(`drivers/uv_updater.py`) and the wiring in `cli.py` own uv and launchd.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class UpdateError(Exception):
    """The upgrade itself could not run (uv missing, `uv tool upgrade` failed).

    A *restart* that fails is not this — that folds into `UpdateResult.detail`
    with `restarted=False`, because the new code is already on disk and a human
    can bounce the service by hand. Only a failure that left the version
    unchanged is an error.
    """


@dataclass(frozen=True)
class UpdateResult:
    """What `update()` did, in the operator's terms.

    `before`/`after` are the version strings the installed `harness --version`
    reported on either side of the upgrade — byte-comparable, so `changed` is
    honest about a no-op poll. `restarted` says whether the service was actually
    bounced; `detail` is the one-line human summary the button renders (why the
    restart was skipped, or that it happened).
    """

    before: str
    after: str
    changed: bool
    restarted: bool
    detail: str


class Updater(ABC):
    """Trigger an in-place upgrade of the running harness."""

    @abstractmethod
    def update(self) -> UpdateResult:
        """Run the upgrade and, if the version changed, restart the service.

        Raises `UpdateError` when the upgrade could not run at all. A restart
        that fails is reported in the result, not raised — the code is already
        installed. Blocking (spawns `uv`); the caller runs it off the event
        loop.
        """
