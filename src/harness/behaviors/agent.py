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

from dataclasses import replace

from harness.artifacts_layout import next_attempt
from harness.models import BehaviorResult, Task
from harness.ports.agent import AgentRunner, AgentSpec
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository
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
        workflows: WorkflowRepository | None = None,
    ) -> None:
        self._clock = clock
        self._workspace = workspace
        self._runner = runner
        self._spec = spec
        self._events = events
        self._timeout = timeout
        self._workflows = workflows

    async def run(self, task: Task) -> BehaviorResult:
        step = task.status or ""
        handle = self._workspace.attach(task)

        # What's left of `ArtifactStore.begin()` from phase 2: scan
        # `.artifacts/<id>/` in the worktree and allocate the next attempt
        # number for this step.
        attempt, relpath = next_attempt(handle.path, task.id, step)

        # The workflow is the live, authoritative source of a step's allowed
        # outcomes (design doc §3) — `spec.allowed_outcomes` is only the
        # fallback for a workflow-less task, an unresolvable workflow
        # reference, or a step with no outgoing edges declared. This is
        # prompt-only *and* enforcement-affecting: the resolved set is fed
        # into `effective_spec` below, so the runner's own verdict check
        # (invariant #13) binds against the same live set as the prompt.
        outcomes = self._spec.allowed_outcomes
        hints: dict[str, str] = {}
        description: str | None = None
        if self._workflows is not None and task.workflow_template:
            try:
                workflow = self._workflows.get(task.workflow_template)
            except WorkflowNotFound:
                workflow = None
            if workflow is not None:
                derived = workflow.outcomes_for(step)
                if derived:
                    outcomes = derived
                    hints = {
                        transition.on: transition.hint
                        for transition in workflow.transitions
                        if transition.from_step == step and transition.hint
                    }
                    description = workflow.description_for(step)

        effective_spec = replace(self._spec, allowed_outcomes=tuple(outcomes))

        prompt = compose_prompt(
            task,
            step=step,
            artifact_relpath=relpath,
            outcomes=effective_spec.allowed_outcomes,
            hints=hints,
            description=description,
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
            spec=effective_spec,
            cwd=handle.path,
            timeout=self._timeout,
            on_output=on_output,
        )

        # The agent only wrote artifacts and code; the worker runs the commit
        # (invariant 9).
        handle.commit(run.summary)
        return BehaviorResult(run.outcome, run.summary)


def compose_prompt(
    task: Task,
    *,
    step: str,
    artifact_relpath: str,
    outcomes: tuple[str, ...],
    hints: dict[str, str],
    description: str | None = None,
) -> str:
    """Build the instruction for the step's agent.

    Concise and deterministic: what the task is (from `task.data`), that it
    should read the previous artifacts in its cwd, where to write its output,
    and how to finish with a machine-readable verdict whose outcome comes from
    `outcomes` — the live, workflow-derived set (or the workflow-less
    fallback), never `spec.allowed_outcomes` read directly.
    """
    request = _request_of(task)
    body = _body_of(task)
    allowed = ", ".join(outcomes)
    artifacts_dir = f".artifacts/{task.id}/"

    lines = [
        f"You are the agent for step '{step}' of task {task.id}.",
    ]
    if description:
        lines.append(f"This step ({step}): {description}")
    lines.extend(
        [
            f"Task: {request}" if request else "The task has no further description.",
            "",
        ]
    )
    if body and body != request:
        lines.extend([body, ""])
    lines.extend(
        [
            f"You'll find the context from previous steps as files in the "
            f"{artifacts_dir} directory in your working directory — read them "
            f"before you start.",
            f"Write your output for this step to the file {artifact_relpath}.",
            "",
            "Finish by choosing exactly one outcome:",
        ]
    )
    for outcome in outcomes:
        hint = hints.get(outcome)
        if hint:
            lines.append(f'  - "{outcome}": {hint}')
        else:
            lines.append(f'  - "{outcome}"')
    lines.extend(
        [
            "",
            "The harness reads your result by machine, not by eye. Your final "
            "message MUST end with exactly this fenced verdict block and nothing "
            "after it — not a prose summary, even once the artifact is written and "
            "the tests pass. A missing block fails the task:",
            "```json",
            '{"outcome": "<one of: ' + allowed + '>", "summary": "<short summary>"}',
            "```",
        ]
    )
    return "\n".join(lines)


def _request_of(task: Task) -> str:
    for key in ("request", "title", "summary"):
        value = task.data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _body_of(task: Task) -> str:
    value = task.data.get("body")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""
