"""Workflow jako <root>/<name>.json."""

from __future__ import annotations

import json
from pathlib import Path

from harness.models import Transition, Workflow
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository


def invalid_workflow_name(name: str) -> bool:
    """Jméno nesmí obsahovat cestový oddělovač a nesmí to být "", "." nebo "..".

    Jediné místo, kde tohle pravidlo žije — `cli.py` ho importuje, aby ho mohlo
    ověřit ještě před zápisem definičního souboru, tedy dřív, než se vůbec
    dostane k této repository."""
    return "/" in name or "\\" in name or name in ("", ".", "..")


class FilesystemWorkflowRepository(WorkflowRepository):
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def get(self, name: str) -> Workflow:
        if invalid_workflow_name(name):
            raise WorkflowNotFound(f"neplatné jméno workflow: {name!r}")

        path = self._root / f"{name}.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise WorkflowNotFound(f"workflow {name!r} neexistuje ({path})") from None
        except json.JSONDecodeError as error:
            raise WorkflowNotFound(
                f"workflow {name!r} má rozbitou definici: {error}"
            ) from None

        if not isinstance(raw, dict):
            raise WorkflowNotFound(
                f"workflow {name!r} má neplatnou definici: očekáván objekt, "
                f"nalezeno {type(raw).__name__}"
            )

        if "start" not in raw:
            raise WorkflowNotFound(f"workflow {name!r} nemá start")

        try:
            transitions = tuple(
                Transition(
                    from_step=item["from"], on=item["on"], to_step=item["to"]
                )
                for item in raw.get("transitions", [])
            )
        except (KeyError, TypeError) as error:
            raise WorkflowNotFound(
                f"workflow {name!r} má neplatný přechod: {error}"
            ) from None

        return Workflow(
            name=raw.get("name", name), start=raw["start"], transitions=transitions
        )
