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

`compile_process` is the single validator: the repository runs it over every
file at startup, and `FilesystemProcessAdmin.write` (the write-side editor for
the admin UI) runs the *identical* code over one submission before writing —
so a process the operator assembles in the dashboard fails exactly where a
hand-edited file would. A `ProcessValidationError` carries an optional `field`
so the admin can map a compile failure to the right form field.

The `sink` field is a forward-compat seam (invariant #40): it is parsed and
validated but restricted to *absent* or `{"kind": "none"}` in v1 (fire-and-forget)
— the door for a real GitHub/Slack reflector is in the wall, but no driver ships
here, so a non-`none` sink is a build error.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from harness.drivers.checks import BUILTIN_CHECKS
from harness.drivers.scheduled_trigger import ScheduledTrigger
from harness.ports.clock import Clock
from harness.ports.process_admin import (
    ProcessAdmin,
    ProcessAdminValidationError,
    ProcessFields,
    ProcessNotFound,
)
from harness.ports.triggers import CheckFactory, parse_interval

_ACCEPTED_SINK_KINDS = {"none"}
"""The only sink kinds v1 accepts. A real reflector adds a kind here plus a driver."""


class ProcessValidationError(Exception):
    """A process file is malformed. The message names the offending file; the
    optional `field` (one of ``interval|check|params|target|dedup|sink`` or
    ``None`` for a whole-file problem like broken JSON) lets the admin map the
    failure onto a form field. `str()` is unchanged — still just the message."""

    def __init__(self, message: str, field: str | None = None) -> None:
        self.field = field
        super().__init__(message)


def invalid_process_name(name: str) -> bool:
    """The name must not carry a path separator and must not be "", "." or ".."."""
    return "/" in name or "\\" in name or name in ("", ".", "..")


class _LocalClock(Clock):
    """A minimal `Clock` for the admin's validation call. `compile_process`
    only *stores* the clock inside the `ScheduledTrigger` it builds — it never
    ticks it — so this exists purely to satisfy the constructor without
    importing a sibling driver (fs_processes stays a thin aggregate, guarded by
    `test_architecture.py`)."""

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    async def sleep(self, seconds: float) -> None:  # pragma: no cover - never called
        return None


def compile_process(
    name: str,
    raw: dict,
    *,
    clock: Clock,
    checks: dict[str, CheckFactory] = BUILTIN_CHECKS,
    repository: str | None = None,
    worktree_root: str | None = None,
    known_targets: set[str] | None = None,
    where: str | None = None,
) -> ScheduledTrigger:
    """Validate one process definition and compile it into a `ScheduledTrigger`.

    The single validator behind both the repository (compile every file at
    startup) and the admin (validate one submission before writing). `where`
    (defaults to `name`) is the label used in messages. Each failure raises
    `ProcessValidationError(msg, field=...)` with `field` one of
    ``interval|check|params|target|dedup|sink``.
    """
    where = where or name
    interval = _parse_interval(where, raw.get("trigger"))
    check = _parse_action(where, raw.get("action"), checks)
    workflow, step = _parse_target(where, raw.get("target"), known_targets)
    dedup = _parse_dedup(where, raw.get("dedup", "per-interval"))
    _validate_sink(where, raw.get("sink"))

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


def _parse_interval(where: str, trigger: object) -> float:
    if not isinstance(trigger, dict) or "interval" not in trigger:
        raise ProcessValidationError(
            f"process {where} must have a trigger with an interval", field="interval"
        )
    try:
        return parse_interval(trigger["interval"])
    except (ValueError, TypeError) as error:
        raise ProcessValidationError(
            f"process {where} has an invalid interval: {error}", field="interval"
        ) from None


def _parse_action(where: str, action: object, checks: dict[str, CheckFactory]):
    if not isinstance(action, dict) or "check" not in action:
        raise ProcessValidationError(
            f"process {where} must have an action naming a check", field="check"
        )
    check_name = action["check"]
    if check_name not in checks:
        raise ProcessValidationError(
            f"process {where} names an unknown check {check_name!r}", field="check"
        )
    try:
        return checks[check_name](action.get("params", {}))
    except (KeyError, ValueError, TypeError) as error:
        raise ProcessValidationError(
            f"process {where} has invalid params for check {check_name!r}: {error}",
            field="params",
        ) from None


def _parse_target(
    where: str,
    target: object,
    known_targets: set[str] | None,
) -> tuple[str | None, str | None]:
    if not isinstance(target, dict) or set(target) not in ({"workflow"}, {"step"}):
        raise ProcessValidationError(
            f"process {where} must have a target of exactly one of "
            f"{{'workflow': ...}} / {{'step': ...}}",
            field="target",
        )

    workflow = target.get("workflow")
    step = target.get("step")
    value = workflow if workflow is not None else step
    if known_targets is not None and value not in known_targets:
        raise ProcessValidationError(
            f"process {where} targets {value!r}, which is not a known "
            f"workflow or step",
            field="target",
        )
    return workflow, step


def _parse_dedup(where: str, dedup: object) -> str:
    if dedup not in ("per-interval", "per-state"):
        raise ProcessValidationError(
            f"process {where} has an invalid dedup strategy: {dedup!r}", field="dedup"
        )
    return dedup


def _validate_sink(where: str, sink: object) -> None:
    # Forward-compat seam (invariant #40): the slot is reserved, but only a
    # fire-and-forget sink ships in v1. A real GitHub/Slack reflector adds an
    # accepted kind here and a driver — no schema change.
    if sink is None:
        return
    if not isinstance(sink, dict) or sink.get("kind") not in _ACCEPTED_SINK_KINDS:
        raise ProcessValidationError(
            f"process {where} has an unsupported sink {sink!r} "
            f"(only {{'kind': 'none'}} is supported in this version)",
            field="sink",
        )


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

        name = raw.get("name", path.stem)
        return compile_process(
            name,
            raw,
            clock=clock,
            checks=checks,
            repository=repository,
            worktree_root=worktree_root,
            known_targets=known_targets,
            where=path.name,
        )


class FilesystemProcessAdmin(ProcessAdmin):
    """Read/write access to the same `<root>/<name>.json` files
    `FilesystemProcessRepository` reads — the admin UI's driver.

    `write` validates a submission with the *same* `compile_process` the
    repository runs at startup, so nothing is written unless it would compile;
    a `ProcessValidationError` (with its `field`) becomes a
    `ProcessAdminValidationError({field: message})`."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def list(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path.stem
                for path in self._root.glob("*.json")
                if not invalid_process_name(path.stem)
            )
        )

    def read(self, name: str) -> ProcessFields:
        if invalid_process_name(name):
            raise ProcessNotFound(f"invalid process name: {name!r}")

        path = self._root / f"{name}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ProcessNotFound(f"process {name!r} does not exist ({path})") from None
        except json.JSONDecodeError as error:
            raise ProcessNotFound(
                f"process {name!r} has a broken definition: {error}"
            ) from None

        try:
            return _fields_from_raw(raw)
        except (KeyError, TypeError, AttributeError) as error:
            raise ProcessNotFound(
                f"process {name!r} has an invalid definition: {error}"
            ) from None

    def write(self, name: str, fields: ProcessFields) -> ProcessFields:
        if invalid_process_name(name):
            raise ProcessAdminValidationError({"name": f"invalid process name: {name!r}"})

        raw = _raw_from_fields(fields)
        try:
            compile_process(name, raw, clock=_LocalClock(), known_targets=None)
        except ProcessValidationError as error:
            raise ProcessAdminValidationError({error.field or "_": str(error)}) from None

        path = self._root / f"{name}.json"
        self._write(path, raw)
        return _fields_from_raw(raw)

    def delete(self, name: str) -> bool:
        if invalid_process_name(name):
            return False
        path = self._root / f"{name}.json"
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def check_names(self) -> tuple[str, ...]:
        return tuple(sorted(BUILTIN_CHECKS))

    def sink_kinds(self) -> tuple[str, ...]:
        return ("none",)

    def _write(self, path: Path, raw: dict) -> None:
        # Same idiom as `FilesystemAgentAdmin._write` (drivers/fs_agents.py):
        # unique temp name in the same directory, then an atomic replace, so a
        # crash mid-write never leaves a half-written JSON file behind.
        temporary = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.json.tmp")
        try:
            temporary.write_text(
                json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


def _raw_from_fields(fields: ProcessFields) -> dict:
    """Assemble the nested `processes/*.json` shape from the flat form fields.
    `name` is never written — the file's stem is the name (like agents)."""
    return {
        "trigger": {"interval": fields.interval},
        "action": {"check": fields.check, "params": dict(fields.params)},
        "target": {fields.target_kind: fields.target},
        "dedup": fields.dedup,
        "sink": {"kind": fields.sink_kind},
    }


def _fields_from_raw(raw: dict) -> ProcessFields:
    """Reconstruct the flat editable fields from a nested file. Raises
    KeyError/TypeError/AttributeError on a malformed shape — the caller (`read`)
    turns that into `ProcessNotFound`."""
    trigger = raw["trigger"]
    action = raw["action"]
    target = raw["target"]
    if "workflow" in target:
        target_kind, target_value = "workflow", target["workflow"]
    elif "step" in target:
        target_kind, target_value = "step", target["step"]
    else:
        raise KeyError("target names neither a workflow nor a step")
    sink_kind = (raw.get("sink") or {}).get("kind", "none")
    return ProcessFields(
        interval=trigger["interval"],
        check=action["check"],
        target_kind=target_kind,
        target=target_value,
        params=dict(action.get("params", {})),
        sink_kind=sink_kind,
        dedup=raw.get("dedup", "per-interval"),
    )
