"""FileIssueBehavior: files a GitHub issue for a confirmed harness bug.

The healer workflow's last step before `end`. Unlike `LandingBehavior` it
touches no worktree and no artifacts — the issue body is assembled purely
from `task.history` entry summaries, the same pattern `LandingBehavior._body`
uses for the PR body. `diagnose`'s persona is what puts the real write-up into
`summary`; this behavior only reassembles it.
"""

from __future__ import annotations

from harness.models import BehaviorResult, Outcome, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.forge import Forge


class FileIssueBehavior(ConsumerBehavior):
    def __init__(self, *, forge: Forge) -> None:
        self._forge = forge

    async def run(self, task: Task) -> BehaviorResult:
        issue = self._forge.open_issue(task, title=self._title(task), body=self._body(task))
        return BehaviorResult(Outcome.DONE, f"filed issue {issue.url}")

    @staticmethod
    def _title(task: Task) -> str:
        failed = task.data.get("failed_task") or {}
        if not failed:
            return f"harness bug found while healing task {task.id}"
        return f"harness bug: task {failed.get('id')} failed at step {failed.get('step')!r}"

    @staticmethod
    def _body(task: Task) -> str:
        lines = ["## Diagnosis", ""]
        for entry in task.history:
            if entry.actor.startswith("consumer:") and entry.summary:
                lines.append(f"- **{entry.from_step}** — {entry.summary}")
        failed = task.data.get("failed_task") or {}
        if failed:
            lines += [
                "",
                "## Original failure",
                "",
                f"- task: {failed.get('id')}",
                f"- workflow: {failed.get('workflow')}",
                f"- step: {failed.get('step')}",
                f"- reason: {failed.get('reason')}",
                f"- repository: {failed.get('repository')}",
            ]
        return "\n".join(lines) + "\n"
