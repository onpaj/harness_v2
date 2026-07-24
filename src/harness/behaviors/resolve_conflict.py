"""`ResolveConflictBehavior` — merges the base branch, hands the agent a real
conflict if there is one, then commits (invariant 9: the worker commits).

The resolver task's own PR branch (`task.data["branch"]`) is already checked
out by `GitWorkspace.attach` before `run()` is called — this behavior only
adds the merge-then-maybe-agent step in front of the same
`AgentRunner`/`AgentSpec`/artifact machinery `ClaudeCliBehavior` uses, so it
stays a dedicated class instead of a branch inside the generic one
(invariant 14: persona is data, not control flow).
"""

from __future__ import annotations

from harness.artifacts_layout import next_attempt
from harness.behaviors.agent import compose_prompt
from harness.models import DONE, BehaviorResult, Task
from harness.ports.agent import AgentRunner, AgentSpec
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.workspace import Workspace


class ResolveConflictBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        clock: Clock,
        workspace: Workspace,
        runner: AgentRunner,
        spec: AgentSpec,
        events: EventSink,
        timeout: float = 600.0,
    ) -> None:
        self._clock = clock
        self._workspace = workspace
        self._runner = runner
        self._spec = spec
        self._events = events
        self._timeout = timeout

    async def run(self, task: Task) -> BehaviorResult:
        step = task.status or ""
        handle = self._workspace.attach(task)
        base = task.data["source"]["base"]

        if not handle.merge(base):
            # The conflict is already gone by the time this task got here (a
            # race: the PR was updated by someone/something else in the
            # meantime) — commit the clean merge result, no agent call spent.
            handle.commit(f"[{step}] merge {base} — no conflicts")
            return BehaviorResult(DONE, f"merged {base} cleanly, no conflicts")

        attempt, relpath = next_attempt(handle.path, task.id, step)
        # Out of Package C's scope: the resolver keeps sourcing its outcomes
        # from `spec.allowed_outcomes` unconditionally (no `WorkflowRepository`
        # threaded in here) — only the shared `compose_prompt` rendering
        # changed underneath it.
        prompt = compose_prompt(
            task,
            step=step,
            artifact_relpath=relpath,
            outcomes=self._spec.allowed_outcomes,
            hints={},
            description=None,
        )

        def on_output(line: str) -> None:
            self._events.emit(
                "stage_output",
                task_id=task.id,
                step=step,
                attempt=attempt,
                line=line,
            )

        run = await self._runner.run(
            prompt=prompt,
            spec=self._spec,
            cwd=handle.path,
            timeout=self._timeout,
            on_output=on_output,
        )

        # The agent only resolved the conflict markers; the worker runs the
        # commit (invariant 9). `git commit` while a merge is in progress
        # (MERGE_HEAD present) produces the two-parent merge commit — no
        # special flag needed.
        handle.commit(run.summary)
        return BehaviorResult(run.outcome, run.summary)
