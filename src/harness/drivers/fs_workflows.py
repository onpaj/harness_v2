"""A workflow as <root>/<name>.json."""

from __future__ import annotations

import json
from pathlib import Path

from harness.models import Transition, Workflow
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository


def invalid_workflow_name(name: str) -> bool:
    """The name must not contain a path separator and must not be "", "." or "..".

    The single place this rule lives — `cli.py` imports it to check the name
    before writing the definition file, i.e. before it ever reaches this
    repository."""
    return "/" in name or "\\" in name or name in ("", ".", "..")


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

        if not isinstance(raw, dict):
            raise WorkflowNotFound(
                f"workflow {name!r} has an invalid definition: expected object, "
                f"got {type(raw).__name__}"
            )

        if "start" not in raw:
            raise WorkflowNotFound(f"workflow {name!r} has no start")

        try:
            transitions = tuple(
                Transition(
                    from_step=item["from"], on=item["on"], to_step=item["to"]
                )
                for item in raw.get("transitions", [])
            )
        except (KeyError, TypeError) as error:
            raise WorkflowNotFound(
                f"workflow {name!r} has an invalid transition: {error}"
            ) from None

        provisional = Workflow(
            name=raw.get("name", name), start=raw["start"], transitions=transitions
        )
        known_steps = set(provisional.steps())

        raw_limits = raw.get("maxParallel", {})
        if not isinstance(raw_limits, dict):
            raise WorkflowNotFound(
                f"workflow {name!r} has an invalid maxParallel: expected object, "
                f"got {type(raw_limits).__name__}"
            )
        max_parallel: dict[str, int] = {}
        for step, limit in raw_limits.items():
            if step not in known_steps:
                raise WorkflowNotFound(
                    f"workflow {name!r} has maxParallel for unknown step {step!r}"
                )
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise WorkflowNotFound(
                    f"workflow {name!r} has invalid maxParallel for step {step!r}: {limit!r}"
                )
            max_parallel[step] = limit

        return Workflow(
            name=raw.get("name", name),
            start=raw["start"],
            transitions=transitions,
            max_parallel=max_parallel,
        )
