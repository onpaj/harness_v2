"""Triggers as data: `<root>/<name>.json` → `ScheduledTrigger`s.

Symmetric to `FilesystemAgentCatalog`/`FilesystemWorkflowRepository`: a trigger
is a small JSON file naming a `kind` (`"scheduled"` only, in v1), an `interval`,
a `check` (resolved against a factory registry, params passed in) and a `target`
that is exactly one of `{"workflow": ...}` / `{"step": ...}`. `build()` reads the
directory once, validates every file up front and fails fast — like a missing
agent spec — raising `TriggerValidationError` (naming the offending file) rather
than letting a malformed trigger fire silently at runtime.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.drivers.checks import BUILTIN_CHECKS
from harness.drivers.scheduled_trigger import ScheduledTrigger
from harness.ports.clock import Clock
from harness.ports.triggers import CheckFactory, parse_interval


class TriggerValidationError(Exception):
    """A trigger file is malformed. The message names the offending file."""


class FilesystemTriggerRepository:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def build(
        self,
        *,
        clock: Clock,
        checks: dict[str, CheckFactory] = BUILTIN_CHECKS,
        repository: str | None = None,
        worktree_root: str | None = None,
        known_targets: set[str] | None = None,
    ) -> list[ScheduledTrigger]:
        if not self._root.exists():
            return []

        triggers: list[ScheduledTrigger] = []
        for path in sorted(self._root.glob("*.json")):
            triggers.append(
                self._build_one(
                    path,
                    clock=clock,
                    checks=checks,
                    repository=repository,
                    worktree_root=worktree_root,
                    known_targets=known_targets,
                )
            )
        return triggers

    def _build_one(
        self,
        path: Path,
        *,
        clock: Clock,
        checks: dict[str, CheckFactory],
        repository: str | None,
        worktree_root: str | None,
        known_targets: set[str] | None,
    ) -> ScheduledTrigger:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise TriggerValidationError(
                f"trigger {path.name} has a broken definition: {error}"
            ) from None

        if not isinstance(raw, dict):
            raise TriggerValidationError(
                f"trigger {path.name} has an invalid definition: expected object, "
                f"got {type(raw).__name__}"
            )

        if raw.get("kind") != "scheduled":
            raise TriggerValidationError(
                f"trigger {path.name} has an unknown kind {raw.get('kind')!r} "
                f"(only 'scheduled' is supported)"
            )

        if "interval" not in raw:
            raise TriggerValidationError(f"trigger {path.name} has no interval")
        try:
            interval = parse_interval(raw["interval"])
        except (ValueError, TypeError) as error:
            raise TriggerValidationError(
                f"trigger {path.name} has an invalid interval: {error}"
            ) from None

        check_name = raw.get("check")
        if check_name not in checks:
            raise TriggerValidationError(
                f"trigger {path.name} names an unknown check {check_name!r}"
            )

        workflow, step = self._parse_target(path, raw.get("target"), known_targets)

        dedup = raw.get("dedup", "per-interval")
        if dedup not in ("per-interval", "per-state"):
            raise TriggerValidationError(
                f"trigger {path.name} has an invalid dedup strategy: {dedup!r}"
            )

        name = raw.get("name", path.stem)
        check = checks[check_name](raw.get("params", {}))
        return ScheduledTrigger(
            name=name,
            clock=clock,
            interval=interval,
            check=check,
            workflow=workflow,
            step=step,
            repository=repository,
            worktree_root=worktree_root,
            dedup=dedup,
        )

    def _parse_target(
        self,
        path: Path,
        target: object,
        known_targets: set[str] | None,
    ) -> tuple[str | None, str | None]:
        if not isinstance(target, dict) or set(target) not in ({"workflow"}, {"step"}):
            raise TriggerValidationError(
                f"trigger {path.name} must have a target of exactly one of "
                f"{{'workflow': ...}} / {{'step': ...}}"
            )

        workflow = target.get("workflow")
        step = target.get("step")
        value = workflow if workflow is not None else step
        if known_targets is not None and value not in known_targets:
            raise TriggerValidationError(
                f"trigger {path.name} targets {value!r}, which is not a known "
                f"workflow or step"
            )
        return workflow, step
