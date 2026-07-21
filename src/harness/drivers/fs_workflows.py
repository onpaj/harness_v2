"""A workflow as <root>/<name>.json."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from harness.models import Transition, Workflow
from harness.ports.workflow_admin import WorkflowAdmin, WorkflowValidationError
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository


def invalid_workflow_name(name: str) -> bool:
    """The name must not contain a path separator and must not be "", "." or "..".

    The single place this rule lives — `cli.py` imports it to check the name
    before writing the definition file, i.e. before it ever reaches this
    repository."""
    return "/" in name or "\\" in name or name in ("", ".", "..")


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

    return Workflow(name=raw.get("name", name), start=raw["start"], transitions=transitions)


class FilesystemWorkflowRepository(WorkflowRepository):
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

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
