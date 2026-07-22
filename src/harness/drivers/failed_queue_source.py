"""FailedQueueTaskSource: turns default-workflow failures into healer tasks.

Reads `failed.list()` only — never claims, moves or mutates the original
task. That is what makes healing a parallel observer rather than a
replacement for `TaskControl.restart`: both can act on the same `failed/`
task independently.

Dedup is delegated entirely to `SourcePoller`/`Task.dedup_key` (no
driver-local ledger): the key is `dedup_key("failed-queue", failure.id,
len(failure.history))`, which changes every time the same task id fails
again (e.g. after an operator `restart`), because `history` strictly grows
by one entry per failure/restart.
"""

from __future__ import annotations

from harness.ids import new_task_id
from harness.models import FAILED, Task
from harness.ports.clock import Clock
from harness.ports.queue import TaskQueue
from harness.ports.source import FinishResult, Progress, TaskSource, dedup_key


class FailedQueueTaskSource(TaskSource):
    kind = "failed-queue"

    def __init__(
        self,
        *,
        failed: TaskQueue,
        clock: Clock,
        target_workflow: str = "default",
        healer_workflow: str = "healer",
        healer_repo: str,
    ) -> None:
        self._failed = failed
        self._clock = clock
        self._target_workflow = target_workflow
        self._healer_workflow = healer_workflow
        self._healer_repo = healer_repo

    def poll(self) -> list[Task]:
        return [
            self._build(failure)
            for failure in self._failed.list()
            if failure.workflow_template == self._target_workflow
        ]

    def _build(self, failure: Task) -> Task:
        step, reason = self._failure_point(failure)
        failed_task = {
            "id": failure.id,
            "workflow": failure.workflow_template,
            "step": step,
            "reason": reason,
            "repository": failure.repository,
        }
        return Task(
            id=new_task_id(),
            workflow_template=self._healer_workflow,
            created=self._clock.now(),
            repository=self._healer_repo,
            dedup_key=dedup_key(self.kind, failure.id, len(failure.history)),
            data={
                "request": self._diagnostic_report(failure, step, reason),
                "failed_task": failed_task,
                "source": {"kind": self.kind, "task": failure.id},
            },
        )

    @staticmethod
    def _diagnostic_report(failure: Task, step: str | None, reason: str | None) -> str:
        return (
            f"Task {failure.id} (workflow {failure.workflow_template!r}) failed "
            f"at step {step!r}.\nReason: {reason}\n\n"
            "Determine whether this was caused by a defect in the harness's own "
            "code, as opposed to the target repository, a flaky agent run, a "
            "transient external error, or an operator error."
        )

    @staticmethod
    def _failure_point(failure: Task) -> tuple[str | None, str | None]:
        """`(from_step, reason)` of the FAILED-labelled history entry — the one
        `Dispatcher._fail`/`Consumer._fail` wrote. `Task` has no separate
        failure-reason field; that entry is the only place it lives."""
        for entry in reversed(failure.history):
            if entry.to_step == FAILED:
                return entry.from_step, entry.reason
        return None, None

    def report_progress(self, task: Task, progress: Progress) -> None:
        pass  # no external system to project a healer task's progress into

    def finish(self, task: Task, result: FinishResult) -> None:
        pass  # ditto
