"""Processes as data: `<root>/<name>.json` → `ScheduledTrigger`s.

A **Process** is the operator's top-level authoring aggregate — it names, as
distinct nested roles, a *trigger* (cadence), an *action* (a named `Check` +
params), a *target* (a workflow **or** a step) and a *sink* (the outbound
reflection destination). It is a *compile-time* concept only: this repository
reads each file and **compiles it into a `ScheduledTrigger`** (a `TaskSource`),
reusing the whole generic-triggers machinery. Nothing under orchestration ever
learns the word "process" (ADR-0015).

Symmetric to `FilesystemTriggerRepository`, but over the richer nested schema:
`build()` reads the directory once, validates every file up front and fails fast
— raising `ProcessValidationError` (naming the offending file) rather than
letting a malformed process fire silently at runtime.

The `sink` field is a forward-compat seam (invariant #40): it is parsed and
validated but restricted to *absent* or `{"kind": "none"}` in v1 (fire-and-forget)
— the door for a real GitHub/Slack reflector is in the wall, but no driver ships
here, so a non-`none` sink is a build error.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.drivers.checks import BUILTIN_CHECKS
from harness.drivers.scheduled_trigger import ScheduledTrigger
from harness.ports.clock import Clock
from harness.ports.triggers import CheckFactory, parse_interval

_ACCEPTED_SINK_KINDS = {"none"}
"""The only sink kinds v1 accepts. A real reflector adds a kind here plus a driver."""


class ProcessValidationError(Exception):
    """A process file is malformed. The message names the offending file."""


class FilesystemProcessRepository:
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
            raise ProcessValidationError(
                f"process {path.name} has a broken definition: {error}"
            ) from None

        if not isinstance(raw, dict):
            raise ProcessValidationError(
                f"process {path.name} has an invalid definition: expected object, "
                f"got {type(raw).__name__}"
            )

        interval = self._parse_interval(path, raw.get("trigger"))
        check = self._parse_action(path, raw.get("action"), checks)
        workflow, step = self._parse_target(path, raw.get("target"), known_targets)
        dedup = self._parse_dedup(path, raw.get("dedup", "per-interval"))
        self._validate_sink(path, raw.get("sink"))

        name = raw.get("name", path.stem)
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

    def _parse_interval(self, path: Path, trigger: object) -> float:
        if not isinstance(trigger, dict) or "interval" not in trigger:
            raise ProcessValidationError(
                f"process {path.name} must have a trigger with an interval"
            )
        try:
            return parse_interval(trigger["interval"])
        except (ValueError, TypeError) as error:
            raise ProcessValidationError(
                f"process {path.name} has an invalid interval: {error}"
            ) from None

    def _parse_action(
        self, path: Path, action: object, checks: dict[str, CheckFactory]
    ):
        if not isinstance(action, dict) or "check" not in action:
            raise ProcessValidationError(
                f"process {path.name} must have an action naming a check"
            )
        check_name = action["check"]
        if check_name not in checks:
            raise ProcessValidationError(
                f"process {path.name} names an unknown check {check_name!r}"
            )
        return checks[check_name](action.get("params", {}))

    def _parse_target(
        self,
        path: Path,
        target: object,
        known_targets: set[str] | None,
    ) -> tuple[str | None, str | None]:
        if not isinstance(target, dict) or set(target) not in ({"workflow"}, {"step"}):
            raise ProcessValidationError(
                f"process {path.name} must have a target of exactly one of "
                f"{{'workflow': ...}} / {{'step': ...}}"
            )

        workflow = target.get("workflow")
        step = target.get("step")
        value = workflow if workflow is not None else step
        if known_targets is not None and value not in known_targets:
            raise ProcessValidationError(
                f"process {path.name} targets {value!r}, which is not a known "
                f"workflow or step"
            )
        return workflow, step

    def _parse_dedup(self, path: Path, dedup: object) -> str:
        if dedup not in ("per-interval", "per-state"):
            raise ProcessValidationError(
                f"process {path.name} has an invalid dedup strategy: {dedup!r}"
            )
        return dedup

    def _validate_sink(self, path: Path, sink: object) -> None:
        # Forward-compat seam (invariant #40): the slot is reserved, but only a
        # fire-and-forget sink ships in v1. A real GitHub/Slack reflector adds an
        # accepted kind here and a driver — no schema change.
        if sink is None:
            return
        if not isinstance(sink, dict) or sink.get("kind") not in _ACCEPTED_SINK_KINDS:
            raise ProcessValidationError(
                f"process {path.name} has an unsupported sink {sink!r} "
                f"(only {{'kind': 'none'}} is supported in this version)"
            )
