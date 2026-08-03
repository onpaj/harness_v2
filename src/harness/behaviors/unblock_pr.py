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

The other thing only this side knows is that the agent **gave up**. `stuck` is a
first-class answer, and it pushes nothing — so the head sha never moves, so the
check never spends an attempt, so its `harness:needs-human` budget can never end
the PR. This behavior therefore applies that label itself (`data`-carried,
through an injected `PrLabeller` — `behaviors/` may not import `drivers/`), which
both tells a human and makes the check skip the PR forever. Without it the
settled task is archived by retention, drops out of `SourcePoller._seen`'s
seeded set, and the identical task is re-minted on the next restart — every
retention window, indefinitely. See ADR-0027.
"""

from __future__ import annotations

from collections.abc import Callable
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

CLEAN = {"resolve": {"clean": True}}
AGENT = {"resolve": {"clean": False}}
"""The two `data.resolve` stamps the delivery report counts apart: the problem
was gone by the time this behavior looked, versus the agent was actually spent
on it. The key is `resolve` rather than `unblock` because `stats.py` has read
it under that name since the retired `ResolveConflictBehavior` wrote it, and a
rename here would silently reclassify every task written before it. Record-only
— `stats.py` treats an unstamped task as the agent path, the conservative read.
See ADR-0026 (the report) and ADR-0027 (this behavior)."""

GIVE_UP_LABEL_KEY = "give_up_label"
"""Where the check leaves the label this behavior applies when the agent gives
up. A plain `data` key, like `branch`/`body`/`problem`, rather than an import:
`behaviors/` may not import `drivers/` (invariant 1), and the *value* travels
in the task precisely so an operator renaming
`processes/unblock-pr.json`'s `give_up_label` renames both halves at once."""

PrLabeller = Callable[[str, int, str], None]
"""`(repo_slug, pr_number, label) -> None`, injected by wiring.

Applying a label needs a `GithubClient`, which is a driver — so this behavior
takes the *capability* rather than the client, exactly as `OpenIssueBehavior`
takes an injected `slug_for`. `cli.py` closes one over the client it already
built when `GITHUB_TOKEN` is set, and passes `None` otherwise: without a token
the `github-unhealthy-prs` check is skipped too, so there is no task to give up
on and nothing to re-mint."""


class UnblockPrError(RuntimeError):
    """A task routed to `unblock` that this behavior cannot read.

    Every task `GithubUnhealthyPrsCheck` mints carries `data.source.base` —
    the branch the PR targets, which the merge needs. A hand-submitted or
    hand-edited one may not, and a bare `KeyError: 'base'` names neither the
    task nor the shape it should have had. `LandingBehavior` avoids the
    question entirely by reading its base through `Forge.base_branch(task)`;
    this behavior has no forge, so it says so itself."""


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
        pr_labeller: PrLabeller | None = None,
    ) -> None:
        self._clock = clock
        self._workspace = workspace
        self._runner = runner
        self._spec = spec
        self._events = events
        self._timeout = timeout
        self._workflows = workflows
        self._pr_labeller = pr_labeller

    async def run(self, task: Task) -> BehaviorResult:
        step = task.status or ""
        # Read before attaching: an unreadable task must fail saying which key
        # it wanted, not after a worktree has been created for it.
        base = _base_branch(task)
        handle = self._workspace.attach(task)
        problem = task.data.get("problem") or {}
        failing = problem.get("failing_checks") or []

        conflicted = handle.merge(base)
        if not conflicted and not failing:
            # The conflict resolved itself between the check emitting and this
            # task running (someone else pushed, or GitHub updated the branch),
            # and nothing was red to begin with. Commit the clean merge; no
            # agent call spent.
            handle.commit(f"[{step}] merge {base} — nothing left to fix")
            return BehaviorResult(
                DONE,
                f"merged {base} cleanly, nothing to fix",
                # Which of the two paths ran is otherwise legible only in the
                # summary's wording, and the delivery report has to tell "the
                # problem evaporated" from "the agent really fixed it"
                # (ADR-0027). Record-only: nothing routes on it (invariant #8).
                data=CLEAN,
            )

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

        summary = run.summary
        if run.outcome != DONE:
            # The agent gave up (`stuck`: "you could not fix this from what you
            # were given — push nothing"). Tell a human, because nothing else
            # will: this path pushes nothing, so the head sha never moves, so
            # the check never spends an attempt and its budget can never reach
            # `harness:needs-human` on its own. The label is also what makes
            # the give-up *terminal* — the check's give-up guard skips a
            # labelled PR on every later tick, which is what stops the settled
            # task being re-minted every time retention archives it (ADR-0027).
            summary = self._label_give_up(task, run.summary)
            if conflicted:
                # Committing would turn the agent's unresolved conflict markers
                # into a two-parent merge commit on a branch that may belong to
                # a human — the exact thing landing already refuses to do when
                # it has no agent to resolve a conflict (`LandingBehavior`,
                # invariant 12). Abandon the merge instead.
                #
                # A *clean* merge is deliberately not aborted here, and the
                # reason is not that there is nothing to abandon — `merge()`
                # runs `git merge --no-commit --no-ff`, so a clean, non-empty
                # merge does leave `MERGE_HEAD` and a staged merge in progress,
                # and the fall-through below commits it. It is that
                # `abort_merge` cannot tell that case from the far commoner one
                # where the base was already merged and git reported "Already
                # up to date" — no `MERGE_HEAD`, and `git merge --abort` then
                # exits non-zero and fails a task whose only sin was a red
                # check. The two-parent commit that shape produces is local
                # only: `stuck` routes straight to `end`, nothing pushes, and
                # the next task's worktree is `reset --hard origin/<branch>`.
                handle.abort_merge()
                return BehaviorResult(run.outcome, summary, data=AGENT)

        # The worker commits, never the agent (invariant 9). `git commit` with
        # MERGE_HEAD present produces the two-parent merge commit — no special
        # flag needed. `.artifacts` is excluded because this branch may belong
        # to a human — the agent's write-up must not ride into somebody else's
        # pull request. The cost of that exclusion is that the write-up is not
        # persisted anywhere on the success path: it stays untracked in the
        # worktree, and `land`'s reattach (`GitWorkspace.attach`, invariant 31)
        # ends in an unconditional `clean -fd` that deletes it. See ADR-0027.
        #
        # Reached on a give-up too, when the merge was clean: nothing to
        # abandon that this behavior can safely detect (see above), and the
        # branch carries the agent's own work in progress. The commit is local
        # — `stuck` routes to `end` and nothing pushes it.
        handle.commit(run.summary, exclude=(".artifacts",))
        return BehaviorResult(run.outcome, summary, data=AGENT)

    def _label_give_up(self, task: Task, summary: str) -> str:
        """Apply the give-up label to the task's pull request, best-effort.

        Best-effort, not fatal: the agent's verdict is real work already done,
        and a transient 5xx on one label call must not turn it into a failed
        task. The cost of swallowing it is that the check re-mints this task
        once retention archives the settled one — the very loop the label
        exists to close — so the failure is written into the summary, where it
        lands in the task's history and on the board rather than in a log
        nobody reads. A no-op when nothing here is configured: no labeller
        wired (no `GITHUB_TOKEN`), no label on the task (hand-submitted), or no
        pull request to label.
        """
        label = task.data.get(GIVE_UP_LABEL_KEY)
        source = task.data.get("source") or {}
        repo, number = source.get("repo"), source.get("pr")
        if self._pr_labeller is None or not label or not repo or number is None:
            return summary
        try:
            self._pr_labeller(repo, number, label)
        except Exception as error:  # noqa: BLE001 - a label is not the work
            return (
                f"{summary} (could not apply {label} to {repo}#{number}: "
                f"{type(error).__name__}: {error})"
            )
        return summary


def _base_branch(task: Task) -> str:
    """The branch the pull request targets, which the merge needs.

    Named rather than subscripted: `LandingBehavior` reads its base through
    `Forge.base_branch(task)` and this behavior has no forge, so an
    unreadable task must still say which key it wanted."""
    base = (task.data.get("source") or {}).get("base")
    if not base:
        raise UnblockPrError(
            f"task {task.id}: no base branch to merge — data['source']['base'] "
            "is missing. Every task GithubUnhealthyPrsCheck mints carries it; a "
            "hand-submitted task routed to this step must supply it too."
        )
    return base


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
