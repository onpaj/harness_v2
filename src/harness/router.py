"""Routovací rozhodnutí. Čistá funkce — žádné I/O, žádný čas, žádný stav."""

from __future__ import annotations

from harness.models import END, Decision, Failed, Finished, MoveTo, Task, Workflow


def route(task: Task, workflow: Workflow) -> Decision:
    """Kam má task jít dál.

    Rozhoduje výhradně podle dvojice (status, lastOutcome). Nikdy nečte
    data, repository ani worktree.
    """
    if task.status is None:
        return MoveTo(workflow.start)

    if task.last_outcome is None:
        return Failed(
            f"task má status {task.status!r}, ale žádný lastOutcome"
        )

    target = workflow.target(task.status, task.last_outcome)
    if target is None:
        return Failed(
            f"workflow {workflow.name!r} nemá hranu z {task.status!r} "
            f"na {task.last_outcome!r}"
        )

    if target == END:
        return Finished()

    return MoveTo(target)
