"""`ClaudeCliBehavior` — delegates the step's work to an agent behind `AgentRunner`.

Replaces `DummyBehavior`: attaches the worktree, computes the attempt number in
`.artifacts/<id>/`, builds the persona prompt, runs the agent, and maps its
verdict 1:1 onto `BehaviorResult`. This worker commits, not the agent
(invariant 9). The behavior **does not branch on the outcome value or the agent
name** (invariants 2, 14) — what distinguishes personas is the contents of the
`AgentSpec`.

Runner exceptions (`AgentError` / `VerdictError` / timeout) are left to bubble up —
the consumer handles them via `_fail` and the task lands in `failed/`.
"""

from __future__ import annotations

from harness.artifacts_layout import next_attempt
from harness.models import BehaviorResult, Task
from harness.ports.agent import AgentRunner, AgentSpec
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.workspace import Workspace


class ClaudeCliBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        clock: Clock,
        workspace: Workspace,
        runner: AgentRunner,
        spec: AgentSpec,
        events: EventSink,
        timeout: float = 1800.0,
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

        # What's left of `ArtifactStore.begin()` from phase 2: scan
        # `.artifacts/<id>/` in the worktree and allocate the next attempt
        # number for this step.
        attempt, relpath = next_attempt(handle.path, task.id, step)

        prompt = compose_prompt(
            task, step=step, artifact_relpath=relpath, spec=self._spec
        )

        # Live stage output: the behavior is the only place that knows the task,
        # step and attempt, so it tags each rendered line the runner streams and
        # emits it as an event. It carries `task_id` (not `task`/`queue`), so the
        # board projection ignores it — the board is unaffected (invariant 7).
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

        # The agent only wrote artifacts and code; the worker runs the commit
        # (invariant 9).
        handle.commit(run.summary)
        return BehaviorResult(run.outcome, run.summary)


def compose_prompt(
    task: Task, *, step: str, artifact_relpath: str, spec: AgentSpec
) -> str:
    """Build the instruction for the step's agent.

    Concise and deterministic: what the task is (from `task.data`), that it
    should read the previous artifacts in its cwd, where to write its output,
    and how to finish with a machine-readable verdict whose outcome comes from
    the allowed set.
    """
    request = _request_of(task)
    allowed = ", ".join(outcome.value for outcome in spec.allowed_outcomes)
    artifacts_dir = f".artifacts/{task.id}/"

    lines = [
        f"You are the agent for step '{step}' of task {task.id}.",
        f"Task: {request}" if request else "The task has no further description.",
        "",
        f"You'll find the context from previous steps as files in the "
        f"{artifacts_dir} directory in your working directory — read them "
        f"before you start.",
        f"Write your output for this step to the file {artifact_relpath}.",
        "",
        "The harness reads your result by machine, not by eye. Your final "
        "message MUST end with exactly this fenced verdict block and nothing "
        "after it — not a prose summary, even once the artifact is written and "
        "the tests pass. A missing block fails the task:",
        "```json",
        '{"outcome": "<one of: ' + allowed + '>", "summary": "<short summary>"}',
        "```",
    ]
    return "\n".join(lines)


def _request_of(task: Task) -> str:
    for key in ("request", "title", "summary"):
        value = task.data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
