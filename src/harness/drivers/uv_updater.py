"""UvUpdater — the real `Updater`, over `uv tool upgrade` + launchd.

This is the same flow `harness update` runs on the CLI (`cli._update`), reached
from the board instead of the shell. It reuses `drivers/launchd.py` for the
restart (driver-to-driver is fine) and discovers `uv` itself; the layout-aware
and packaging-aware bits (the installed entry point, the idle gate) are injected
by the wiring in `cli.py`, so this driver stays free of any import back into
`cli.py` (which would cycle — `cli.py` imports this driver).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from harness.drivers.launchd import ServiceError, kickstart
from harness.ports.updater import Updater, UpdateError, UpdateResult


class UvUpdater(Updater):
    def __init__(
        self,
        *,
        package: str,
        entry_point: Path,
        uid: int,
        label: str,
        is_stage_active: Callable[[], list[str]],
        uv_path: Path | None = None,
    ) -> None:
        self._package = package
        self._entry_point = entry_point
        self._uid = uid
        self._label = label
        self._is_stage_active = is_stage_active
        self._uv_path = uv_path

    def update(self) -> UpdateResult:
        uv = self._uv()
        if uv is None:
            raise UpdateError(
                "uv is not installed — install it with\n"
                "  curl -LsSf https://astral.sh/uv/install.sh | sh"
            )

        before = self._version_report()
        result = subprocess.run(
            [str(uv), "tool", "upgrade", self._package],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise UpdateError(
                f"uv tool upgrade failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

        after = self._version_report()
        if before == after:
            return UpdateResult(
                before=before,
                after=after,
                changed=False,
                restarted=False,
                detail=f"already up to date ({after})",
            )

        active = self._is_stage_active()
        if active:
            return UpdateResult(
                before=before,
                after=after,
                changed=True,
                restarted=False,
                detail=(
                    f"updated to {after}; a stage is running ({', '.join(active)}) "
                    "— restart deferred, the update applies at the next idle restart"
                ),
            )

        try:
            kickstart(self._uid, self._label)
        except ServiceError as error:
            return UpdateResult(
                before=before,
                after=after,
                changed=True,
                restarted=False,
                detail=(
                    f"updated to {after}; automatic restart failed ({error}) "
                    f"— restart the service manually"
                ),
            )
        return UpdateResult(
            before=before,
            after=after,
            changed=True,
            restarted=True,
            detail=f"updated to {after}; restarting service {self._label}…",
        )

    def _uv(self) -> Path | None:
        """The `uv` binary, or None. Mirrors `cli.uv_executable` — checked
        explicitly rather than trusting `PATH`, since the service builds its own."""
        if self._uv_path is not None:
            return self._uv_path
        found = shutil.which("uv")
        if found:
            return Path(found)
        candidate = Path.home() / ".local" / "bin" / "uv"
        return candidate if candidate.exists() else None

    def _version_report(self) -> str:
        """Ask the installed `harness` script what it is now. Shelled out (not
        read from our own already-stale metadata) because after the upgrade this
        process is still the version it replaced — mirrors
        `cli.installed_version_report`."""
        entry = self._entry_point
        if not entry.is_file():
            return "harness (installed; run `harness --version` to confirm)"
        result = subprocess.run(
            [str(entry), "--version"], capture_output=True, text=True, check=False
        )
        reported = result.stdout.strip()
        if result.returncode != 0 or not reported:
            return "harness (installed; run `harness --version` to confirm)"
        return reported
