"""Agent catalog as `<root>/<name>.json`.

Symmetric to `FilesystemWorkflowRepository`: step name → persona as data.
The file carries **our** format (not CLI flags), so the catalog is the single
source of truth:

    {
        "prompt": "...",
        "model": null,
        "fallback_model": null,
        "allowed_tools": [],
        "allowed_outcomes": ["done", "request_changes"],
        "timeout": null
    }

Missing file / broken JSON / invalid name → `AgentNotFound`.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from harness.models import Outcome
from harness.ports.agent import AgentCatalog, AgentNotFound, AgentSpec
from harness.ports.agent_admin import AgentAdmin, AgentFields, AgentValidationError


def invalid_agent_name(name: str) -> bool:
    """The name must not carry a path separator and must not be "", "." or ".."."""
    return "/" in name or "\\" in name or name in ("", ".", "..")


def _parse_agent_spec(name: str, raw: dict) -> AgentSpec:
    """Raises ValueError on a missing prompt or invalid allowed_outcomes — the
    single validation contract shared by the read path (`.get`) and the write
    path (`AgentAdmin.write`)."""
    if not isinstance(raw, dict):
        raise ValueError(
            f"agent {name!r} has an invalid definition: expected object, "
            f"got {type(raw).__name__}"
        )

    if "prompt" not in raw:
        raise ValueError(f"agent {name!r} has no prompt")

    try:
        allowed_outcomes = tuple(
            Outcome(item) for item in raw.get("allowed_outcomes", ["done"])
        )
    except (ValueError, TypeError) as error:
        raise ValueError(f"agent {name!r} has invalid allowed_outcomes: {error}") from None

    timeout = raw.get("timeout")
    if timeout is not None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise ValueError(
                f"agent {name!r} has invalid timeout: expected a positive "
                f"number, got {timeout!r}"
            )
        if timeout <= 0:
            raise ValueError(
                f"agent {name!r} has invalid timeout: expected a positive "
                f"number, got {timeout!r}"
            )
        timeout = float(timeout)

    return AgentSpec(
        name=name,
        prompt=raw["prompt"],
        model=raw.get("model"),
        fallback_model=raw.get("fallback_model"),
        allowed_tools=tuple(raw.get("allowed_tools", ())),
        allowed_outcomes=allowed_outcomes,
        timeout=timeout,
    )


class FilesystemAgentCatalog(AgentCatalog):
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def names(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path.stem
                for path in self._root.glob("*.json")
                if not invalid_agent_name(path.stem)
            )
        )

    def get(self, name: str) -> AgentSpec:
        if invalid_agent_name(name):
            raise AgentNotFound(f"invalid agent name: {name!r}")

        path = self._root / f"{name}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise AgentNotFound(f"agent {name!r} does not exist ({path})") from None
        except json.JSONDecodeError as error:
            raise AgentNotFound(
                f"agent {name!r} has a broken definition: {error}"
            ) from None

        try:
            return _parse_agent_spec(name, raw)
        except ValueError as error:
            raise AgentNotFound(str(error)) from None


class FilesystemAgentAdmin(AgentAdmin):
    """Read/write access to the same `<root>/<name>.json` files
    `FilesystemAgentCatalog` reads — the admin UI's driver."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def list(self) -> tuple[str, ...]:
        return tuple(sorted(path.stem for path in self._root.glob("*.json")))

    def read(self, name: str) -> AgentSpec:
        if invalid_agent_name(name):
            raise AgentNotFound(f"invalid agent name: {name!r}")

        path = self._root / f"{name}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise AgentNotFound(f"agent {name!r} does not exist ({path})") from None
        except json.JSONDecodeError as error:
            raise AgentNotFound(
                f"agent {name!r} has a broken definition: {error}"
            ) from None

        try:
            return _parse_agent_spec(name, raw)
        except ValueError as error:
            raise AgentNotFound(str(error)) from None

    def write(self, name: str, fields: AgentFields) -> AgentSpec:
        if invalid_agent_name(name):
            raise AgentValidationError({"name": f"invalid agent name: {name!r}"})
        if not fields.prompt:
            # `_parse_agent_spec` only checks key *presence* (its contract is
            # unchanged by this refactor, per the read path it also serves),
            # but `AgentFields.prompt` is always present as a key once built
            # from a form/JSON body — an empty string is how "the operator
            # left it blank" actually arrives here, so it's checked explicitly.
            raise AgentValidationError({"prompt": "prompt is required"})

        raw = {
            "prompt": fields.prompt,
            "model": fields.model,
            "fallback_model": fields.fallback_model,
            "allowed_tools": list(fields.allowed_tools),
            "allowed_outcomes": list(fields.allowed_outcomes),
        }
        try:
            spec = _parse_agent_spec(name, raw)
        except ValueError as error:
            raise AgentValidationError({"prompt": str(error)}) from None

        path = self._root / f"{name}.json"
        self._write(path, raw)
        return spec

    def delete(self, name: str) -> bool:
        if invalid_agent_name(name):
            return False
        path = self._root / f"{name}.json"
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _write(self, path: Path, raw: dict) -> None:
        # Same idiom as `FilesystemTaskQueue._write` (drivers/fs_queue.py):
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
