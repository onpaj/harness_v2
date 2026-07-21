"""Agent catalog as `<root>/<name>.json`.

Symmetric to `FilesystemWorkflowRepository`: step name → persona as data.
The file carries **our** format (not CLI flags), so the catalog is the single
source of truth:

    {
        "prompt": "...",
        "model": null,
        "fallback_model": null,
        "allowed_tools": [],
        "allowed_outcomes": ["done", "request_changes"]
    }

Missing file / broken JSON / invalid name → `AgentNotFound`.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.models import Outcome
from harness.ports.agent import AgentCatalog, AgentNotFound, AgentSpec


def _invalid_agent_name(name: str) -> bool:
    """The name must not carry a path separator and must not be "", "." or ".."."""
    return "/" in name or "\\" in name or name in ("", ".", "..")


class FilesystemAgentCatalog(AgentCatalog):
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def names(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                path.stem
                for path in self._root.glob("*.json")
                if not _invalid_agent_name(path.stem)
            )
        )

    def get(self, name: str) -> AgentSpec:
        if _invalid_agent_name(name):
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

        if not isinstance(raw, dict):
            raise AgentNotFound(
                f"agent {name!r} has an invalid definition: expected object, "
                f"got {type(raw).__name__}"
            )

        if "prompt" not in raw:
            raise AgentNotFound(f"agent {name!r} has no prompt")

        try:
            allowed_outcomes = tuple(
                Outcome(item) for item in raw.get("allowed_outcomes", ["done"])
            )
        except (ValueError, TypeError) as error:
            raise AgentNotFound(
                f"agent {name!r} has invalid allowed_outcomes: {error}"
            ) from None

        return AgentSpec(
            name=name,
            prompt=raw["prompt"],
            model=raw.get("model"),
            fallback_model=raw.get("fallback_model"),
            allowed_tools=tuple(raw.get("allowed_tools", ())),
            allowed_outcomes=allowed_outcomes,
        )
