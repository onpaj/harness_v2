"""VerifyBehavior — the deterministic test gate between development and review.

"Landing is a step, not magic" (ADR-0009) applied to verification: the step is
a normal `ConsumerBehavior`, but no LLM is involved — the repo's `verify`
command (from `repos.json`) runs in the worktree and the exit code IS the
verdict. Extends invariant 9's philosophy: test results are established by a
driver, not claimed by an agent.

Outcome mapping: exit 0 → done; non-zero → request_changes (the workflow's
back edge gives development another round, with the failure tail in the
summary and the full output in an attempt-indexed artifact). A timeout or
runner crash raises — the consumer fails the task into `failed/`; a red suite
is request_changes, never failed.

A task with no repository, or a repo with no `verify` command configured, is
a `done` no-op — the gate is opt-in per repo (spec: increment 1).
"""

from __future__ import annotations

from harness.artifacts_layout import next_attempt
from harness.models import DONE, REQUEST_CHANGES, BehaviorResult, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.command import CommandRunner
from harness.ports.repos import RepositoryRegistry
from harness.ports.workspace import Workspace

# How much of the command output the summary keeps (the history line / next
# development round's context). The full output is always in the artifact.
TAIL_CHARS = 1500


class VerifyBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        workspace: Workspace,
        registry: RepositoryRegistry | None,
        runner: CommandRunner,
        timeout: float = 900.0,
    ) -> None:
        self._workspace = workspace
        self._registry = registry
        self._runner = runner
        self._timeout = timeout

    async def run(self, task: Task) -> BehaviorResult:
        step = task.status or "verify"

        command = None
        if task.repository and self._registry is not None:
            command = self._registry.verify_command(task.repository)
        if not command:
            return BehaviorResult(
                DONE, "verify skipped: no verify command configured for this repo"
            )

        handle = self._workspace.attach(task)
        attempt, relpath = next_attempt(handle.path, task.id, step)

        result = await self._runner.run(
            command, cwd=handle.path, timeout=self._timeout
        )

        artifact = (
            f"# verify — attempt {attempt:02d}\n\n"
            f"Command: `{command}`\n\n"
            f"Exit code: {result.exit_code}\n\n"
            "```text\n"
            f"{result.output}\n"
            "```\n"
        )
        handle.write(relpath, artifact)
        handle.commit(f"[verify] attempt {attempt:02d}: exit {result.exit_code}")

        if result.exit_code == 0:
            return BehaviorResult(DONE, f"verify passed: `{command}` (exit 0)")
        tail = result.output[-TAIL_CHARS:].strip()
        return BehaviorResult(
            REQUEST_CHANGES,
            f"verify failed (`{command}` exit {result.exit_code}):\n{tail}",
        )
