"""`UnblockPrBehavior` — merges the base branch, hands the agent whatever is
wrong with the pull request, then commits (invariant 9: the worker commits).

The unblock task's own PR branch (`task.data["branch"]`) is already checked out
by `GitWorkspace.attach` before `run()` is called. This behavior adds the
merge-then-brief-then-commit step in front of the same
`AgentRunner`/`AgentSpec`/artifact machinery `ClaudeCliBehavior` uses, so it
stays a dedicated class instead of a branch inside the generic one
(invariant 14: persona is data, not control flow).

The brief itself needs no code here: `GithubUnhealthyPrsCheck` renders it into
`data.body`, which `compose_prompt` already puts in front of the agent.
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


class UnblockPrBehavior(ConsumerBehavior):
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
        problem = task.data.get("problem") or {}
        failing = problem.get("failing_checks") or []

        conflicted = handle.merge(base)
        if not conflicted and not failing:
            # The conflict resolved itself between the check emitting and this
            # task running (someone else pushed, or GitHub updated the branch),
            # and nothing was red to begin with. Commit the clean merge; no
            # agent call spent.
            handle.commit(f"[{step}] merge {base} — nothing left to fix")
            return BehaviorResult(DONE, f"merged {base} cleanly, nothing to fix")

        attempt, relpath = next_attempt(handle.path, task.id, step)
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

        # The worker commits, never the agent (invariant 9). `git commit` with
        # MERGE_HEAD present produces the two-parent merge commit — no special
        # flag needed. `.artifacts` is excluded because this branch may belong
        # to a human: the agent's write-up belongs in the task record, not in
        # somebody else's pull request.
        handle.commit(run.summary, exclude=(".artifacts",))
        return BehaviorResult(run.outcome, run.summary)
