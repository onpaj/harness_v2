"""Katalog agentů jako `<root>/<name>.json`.

Symetricky k `FilesystemWorkflowRepository`: jméno kroku → persona jako data.
Soubor nese **náš** formát (ne CLI flagy), ať je katalog jediný zdroj pravdy:

    {
        "prompt": "...",
        "model": null,
        "fallback_model": null,
        "allowed_tools": [],
        "allowed_outcomes": ["done", "request_changes"]
    }

Chybějící soubor / rozbitý JSON / neplatné jméno → `AgentNotFound`.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.models import Outcome
from harness.ports.agent import AgentCatalog, AgentNotFound, AgentSpec


def _invalid_agent_name(name: str) -> bool:
    """Jméno nesmí nést cestový oddělovač a nesmí to být "", "." nebo ".."."""
    return "/" in name or "\\" in name or name in ("", ".", "..")


class FilesystemAgentCatalog(AgentCatalog):
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def get(self, name: str) -> AgentSpec:
        if _invalid_agent_name(name):
            raise AgentNotFound(f"neplatné jméno agenta: {name!r}")

        path = self._root / f"{name}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise AgentNotFound(f"agent {name!r} neexistuje ({path})") from None
        except json.JSONDecodeError as error:
            raise AgentNotFound(
                f"agent {name!r} má rozbitou definici: {error}"
            ) from None

        if not isinstance(raw, dict):
            raise AgentNotFound(
                f"agent {name!r} má neplatnou definici: očekáván objekt, "
                f"nalezeno {type(raw).__name__}"
            )

        if "prompt" not in raw:
            raise AgentNotFound(f"agent {name!r} nemá prompt")

        try:
            allowed_outcomes = tuple(
                Outcome(item) for item in raw.get("allowed_outcomes", ["done"])
            )
        except (ValueError, TypeError) as error:
            raise AgentNotFound(
                f"agent {name!r} má neplatný allowed_outcomes: {error}"
            ) from None

        return AgentSpec(
            name=name,
            prompt=raw["prompt"],
            model=raw.get("model"),
            fallback_model=raw.get("fallback_model"),
            allowed_tools=tuple(raw.get("allowed_tools", ())),
            allowed_outcomes=allowed_outcomes,
        )
