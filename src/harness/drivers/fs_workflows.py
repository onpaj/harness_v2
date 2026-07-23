"""A workflow as <root>/<name>.json."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Sequence
from pathlib import Path

from harness.models import END, FAILED, Transition, Workflow
from harness.ports.board import DONE_COLUMN, TODO_COLUMN
from harness.ports.workflow_admin import WorkflowAdmin, WorkflowValidationError
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository


def invalid_workflow_name(name: str) -> bool:
    """The name must not contain a path separator and must not be "", "." or "..".

    The single place this rule lives — `cli.py` imports it to check the name
    before writing the definition file, i.e. before it ever reaches this
    repository."""
    return "/" in name or "\\" in name or name in ("", ".", "..")


_RESERVED_STEP_NAMES = (END, FAILED, DONE_COLUMN, TODO_COLUMN)


def invalid_step_name(name: str) -> bool:
    """Everything invalid_workflow_name rejects, plus the reserved board-level
    names (`end`, `failed`, `done`, `todo`) — a step name now comes straight
    from `--step`/a TaskSource, not only from inside a trusted workflow file,
    so it must not collide with a board column."""
    return invalid_workflow_name(name) or name in _RESERVED_STEP_NAMES


def _parse_workflow(name: str, raw: dict) -> Workflow:
    """Raises ValueError for a non-dict body, a missing `start`, or a
    malformed transition — the single validation contract shared by the read
    path (`.get`) and the write path (`WorkflowAdmin.write_raw`)."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"workflow {name!r} has an invalid definition: expected object, "
            f"got {type(raw).__name__}"
        )

    if "start" not in raw:
        raise ValueError(f"workflow {name!r} has no start")

    try:
        transitions = tuple(
            Transition(from_step=item["from"], on=item["on"], to_step=item["to"])
            for item in raw.get("transitions", [])
        )
    except (KeyError, TypeError) as error:
        raise ValueError(f"workflow {name!r} has an invalid transition: {error}") from None

    provisional = Workflow(
        name=raw.get("name", name), start=raw["start"], transitions=transitions
    )
    known_steps = set(provisional.steps())

    raw_limits = raw.get("maxParallel", {})
    if not isinstance(raw_limits, dict):
        raise ValueError(
            f"workflow {name!r} has an invalid maxParallel: expected object, "
            f"got {type(raw_limits).__name__}"
        )
    max_parallel: dict[str, int] = {}
    for step, limit in raw_limits.items():
        if step not in known_steps:
            raise ValueError(
                f"workflow {name!r} has maxParallel for unknown step {step!r}"
            )
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError(
                f"workflow {name!r} has invalid maxParallel for step {step!r}: {limit!r}"
            )
        max_parallel[step] = limit

    # `finishers` maps a step name to a finisher kind (ADR-0016) — validated
    # here, exactly like `maxParallel`, so both the read path and the admin
    # write path reject a bad binding through this one shared contract. The
    # kind itself is resolved against the registry at build time (`app.build`),
    # not here: the file doesn't know which kinds this harness wires.
    raw_finishers = raw.get("finishers", {})
    if not isinstance(raw_finishers, dict):
        raise ValueError(
            f"workflow {name!r} has an invalid finishers: expected object, "
            f"got {type(raw_finishers).__name__}"
        )
    finishers: dict[str, str] = {}
    for step, kind in raw_finishers.items():
        if step not in known_steps:
            raise ValueError(
                f"workflow {name!r} has a finisher for unknown step {step!r}"
            )
        if not isinstance(kind, str) or not kind:
            raise ValueError(
                f"workflow {name!r} has an invalid finisher for step {step!r}: {kind!r}"
            )
        finishers[step] = kind

    return Workflow(
        name=raw.get("name", name),
        start=raw["start"],
        transitions=transitions,
        max_parallel=max_parallel,
        finishers=finishers,
    )


class FilesystemWorkflowRepository(WorkflowRepository):
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def names(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path.stem
                for path in self._root.glob("*.json")
                if not invalid_workflow_name(path.stem)
            )
        )

    def get(self, name: str) -> Workflow:
        if invalid_workflow_name(name):
            raise WorkflowNotFound(f"invalid workflow name: {name!r}")

        path = self._root / f"{name}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise WorkflowNotFound(f"workflow {name!r} does not exist ({path})") from None
        except json.JSONDecodeError as error:
            raise WorkflowNotFound(
                f"workflow {name!r} has a broken definition: {error}"
            ) from None

        try:
            return _parse_workflow(name, raw)
        except ValueError as error:
            raise WorkflowNotFound(str(error)) from None


class FilesystemWorkflowAdmin(WorkflowAdmin):
    """Read/write access to the same `<root>/<name>.json` files
    `FilesystemWorkflowRepository` reads — the admin UI's driver. Keeps the
    file's raw text end to end: never re-serializes what the operator wrote."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def list(self) -> tuple[str, ...]:
        return tuple(sorted(path.stem for path in self._root.glob("*.json")))

    def read_raw(self, name: str) -> str:
        if invalid_workflow_name(name):
            raise WorkflowNotFound(f"invalid workflow name: {name!r}")

        path = self._root / f"{name}.json"
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise WorkflowNotFound(f"workflow {name!r} does not exist ({path})") from None

    def write_raw(self, name: str, raw_json: str) -> None:
        if invalid_workflow_name(name):
            raise WorkflowValidationError(f"invalid workflow name: {name!r}")

        try:
            raw = json.loads(raw_json)
        except json.JSONDecodeError as error:
            raise WorkflowValidationError(str(error)) from None

        try:
            _parse_workflow(name, raw)
        except ValueError as error:
            raise WorkflowValidationError(str(error)) from None

        path = self._root / f"{name}.json"
        self._write(path, raw_json)

    def delete(self, name: str) -> bool:
        if invalid_workflow_name(name):
            return False
        path = self._root / f"{name}.json"
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _write(self, path: Path, text: str) -> None:
        # Same idiom as `FilesystemTaskQueue._write` (drivers/fs_queue.py):
        # unique temp name in the same directory, then an atomic replace, so a
        # crash mid-write never leaves a half-written JSON file behind.
        temporary = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.json.tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise


class ServedWorkflowRepository(WorkflowRepository):
    """Restricts an inner repository to a fixed served set.

    A name outside the set fails with WorkflowNotFound, the same exception
    type and dispatcher failure path as a genuinely missing workflow — so no
    dispatcher change is needed to make an unserved workflow fail fast.
    """

    def __init__(self, inner: WorkflowRepository, names: Sequence[str]) -> None:
        self._inner = inner
        self._served = tuple(dict.fromkeys(names))

    def get(self, name: str) -> Workflow:
        if name not in self._served:
            served = ", ".join(self._served) or "(none)"
            raise WorkflowNotFound(
                f"workflow {name!r} is not served by this harness (served: {served})"
            )
        return self._inner.get(name)

    def names(self) -> tuple[str, ...]:
        return self._served
