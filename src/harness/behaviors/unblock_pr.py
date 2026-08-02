"""`UnblockPrBehavior` — merges the base branch, hands the agent whatever is
wrong with the pull request, then commits (invariant 9: the worker commits).

The unblock task's own PR branch (`task.data["branch"]`) is already checked out
by `GitWorkspace.attach` before `run()` is called. This behavior adds the
merge-then-brief-then-commit step in front of the same
`AgentRunner`/`AgentSpec`/artifact machinery `ClaudeCliBehavior` uses, so it
stays a dedicated class instead of a branch inside the generic one
(invariant 14: persona is data, not control flow).

Most of the brief needs no code here: `GithubUnhealthyPrsCheck` renders it into
`data.body`, which `compose_prompt` already puts in front of the agent. The one
thing this behavior knows that the check could not is whether the merge it just
ran actually conflicts — the check's flag was true at *scan* time, and the base
moves — so a disagreement between the two is amended onto the brief before the
prompt is composed.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from harness.artifacts_layout import next_attempt
from harness.behaviors.agent import compose_prompt, resolve_step_vocabulary
from harness.models import DONE, BehaviorResult, Task
from harness.ports.agent import AgentRunner, AgentSpec
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.workflows import WorkflowRepository
from harness.ports.workspace import Workspace

CONFLICT_APPEARED = (
    "**The branch conflicts with its base.** The base has already been merged "
    "into your working directory, so the conflict markers are in the files in "
    "front of you."
)
"""Word-for-word what `github_unhealthy_prs_check._render_brief` writes for a
conflict it saw itself, so the agent reads the same sentence whether the
conflict was known at scan time or appeared afterwards. Duplicated rather than
imported: `behaviors/` may not import `drivers/` (invariant 1)."""

CONFLICT_GONE = (
    "**The brief above says this branch conflicts with its base — it no longer "
    "does.** The base merged cleanly into your working directory just now, so "
    "there are no conflict markers to look for; fix only what else is listed."
)
"""The mirror case. The brief was rendered at scan time and someone resolved
the conflict since, so the agent would otherwise hunt for markers that are not
there."""


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

        # Invariant 42: the workflow, not the persona file, declares what this
        # step may report — and it also authors the per-edge hints and the
        # step description. Resolved exactly the way `ClaudeCliBehavior` does
        # (same helper), so `stuck`'s "push nothing" hint actually reaches the
        # agent instead of being silently discarded.
        vocabulary = resolve_step_vocabulary(
            workflows=self._workflows,
            task=task,
            step=step,
            fallback=self._spec.allowed_outcomes,
        )
        effective_spec = replace(
            self._spec, allowed_outcomes=tuple(vocabulary.outcomes)
        )

        prompt = compose_prompt(
            _briefed(task, problem, conflicted),
            step=step,
            artifact_relpath=relpath,
            outcomes=effective_spec.allowed_outcomes,
            hints=vocabulary.hints,
            description=vocabulary.description,
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
            spec=effective_spec,
            cwd=handle.path,
            timeout=self._timeout,
            on_output=on_output,
        )

        if conflicted and run.outcome != DONE:
            # The agent gave up (`stuck`: "you could not fix this from what you
            # were given — push nothing"). Committing anyway would turn its
            # unresolved conflict markers into a two-parent merge commit on a
            # branch that may belong to a human — the exact thing landing
            # already refuses to do when it has no agent to resolve a conflict
            # (`LandingBehavior`, invariant 12). Abandon the merge instead.
            handle.abort_merge()
            return BehaviorResult(run.outcome, run.summary)

        # The worker commits, never the agent (invariant 9). `git commit` with
        # MERGE_HEAD present produces the two-parent merge commit — no special
        # flag needed. `.artifacts` is excluded because this branch may belong
        # to a human — the agent's write-up must not ride into somebody else's
        # pull request. The cost of that exclusion is that the write-up is not
        # persisted anywhere on the success path: it stays untracked in the
        # worktree, and `land`'s reattach (`GitWorkspace.attach`, invariant 31)
        # ends in an unconditional `clean -fd` that deletes it. See ADR-0026.
        handle.commit(run.summary, exclude=(".artifacts",))
        return BehaviorResult(run.outcome, run.summary)


def _briefed(task: Task, problem: dict[str, Any], conflicted: bool) -> Task:
    """The task as the agent should see it: `data.body` amended when the merge
    that just ran disagrees with the conflict flag the check recorded.

    The check rendered `data.body` at scan time from `problem["conflicted"]`;
    the base can move between then and now. Without this, a conflict that
    appeared in between reaches the agent as a working tree full of `<<<<<<<`
    markers and a brief that never mentions them — and the worker's commit
    would then merge the markers onto the branch.
    """
    recorded = problem.get("conflicted")
    if recorded is None or bool(recorded) == conflicted:
        return task
    note = CONFLICT_APPEARED if conflicted else CONFLICT_GONE
    body = task.data.get("body")
    body = f"{body}\n\n{note}" if isinstance(body, str) and body.strip() else note
    return replace(task, data={**task.data, "body": body})
