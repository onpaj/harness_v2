"""Routing decision. A pure function — no I/O, no time, no state."""

from __future__ import annotations

from harness.models import END, Decision, Failed, Finished, MoveTo, Task, Workflow


def route(task: Task, workflow: Workflow) -> Decision:
    """Where the task should go next.

    Decides solely from the pair (status, lastOutcome). It never reads
    data, repository, or worktree.
    """
    if task.status is None:
        return MoveTo(workflow.start)

    if task.last_outcome is None:
        return Failed(
            f"task has status {task.status!r} but no lastOutcome"
        )

    target = workflow.target(task.status, task.last_outcome)
    if target is None:
        return Failed(
            f"workflow {workflow.name!r} has no edge from {task.status!r} "
            f"on {task.last_outcome!r}"
        )

    if target == END:
        return Finished()

    return MoveTo(target)
