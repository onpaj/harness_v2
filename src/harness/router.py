"""Routing decision. A pure function — no I/O, no time, no state."""

from __future__ import annotations

from harness.models import END, FAILED, Decision, Failed, Finished, MoveTo, Task, Workflow


def route(task: Task, workflow: Workflow | None) -> Decision:
    """Where the task should go next.

    Decides solely from the pair (status, lastOutcome) — plus, since a
    workflow is now optional, whether one is present at all. It never reads
    data, repository, or worktree.
    """
    if task.status is None:
        if workflow is not None:
            return MoveTo(workflow.start)
        if task.step is None or task.step in (END, FAILED):
            return Failed("workflow-less task has no usable step")
        return MoveTo(task.step)

    if task.last_outcome is None:
        return Failed(
            f"task has status {task.status!r} but no lastOutcome"
        )

    if workflow is None:
        return Finished()

    if task.status not in workflow.steps():
        return Failed(f"workflow {workflow.name!r} has no step {task.status!r}")

    target = workflow.target(task.status, task.last_outcome)
    if target is None:
        return Finished()

    if target == END:
        return Finished()

    return MoveTo(target)
