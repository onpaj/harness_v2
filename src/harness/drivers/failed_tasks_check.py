"""`FailedTasksCheck`: the self-heal action, expressed as a process `Check`.

The inbound half of self-healing (ADR-0018), mirroring `GithubIssuesCheck`'s
shape: it claims work out of an existing queue (`failed/`) via the ordinary
atomic `claim()` — the same "idempotent, side-effecting claim action" a
`TaskSource.poll()` may perform (invariant #35) — and turns each claim into one
`Observation` that the compiled `ScheduledTrigger` fires as a fresh `heal`
task.

A claimed failure settles onto **`done/`**, not onto a terminal of its own
(ADR-0024): once the healer has taken the failure over, the original task's own
story is finished, and the board should say so in the one column an operator
already reads as "finished, nothing to do here". That the healer — rather than
the workflow — ended it is recorded in the task's history, as the `failed-tasks`
actor moving it from `failed` to `end`. Each failure is still claimed exactly
once, so it can never be healed twice (invariant 25).

Unlike `GithubIssuesCheck`, this needs no external client — it needs the
harness's own live `failed`/`done` queues and event sink, so it is registered
inside `app.build()` itself (§FR-6), not via a `cli.py` factory.

Recursion guard: a failure is *declined* — never claimed into the heal
pipeline, no `Observation` — in three cases. `data.heal` marks it as itself a
`heal` workflow task that failed (`heal-failed`, so a stuck healer can never
re-enter `failed/` a second time). `data["body"]` carrying the
`<!-- harness-issue:… -->` marker marks it as a fix attempt for an issue the
harness itself filed (`heal-declined`). `data["source"]["kind"]` being
`"mergeability"` or `"pull-request"` marks it as a resolver or
automerge-review task minted from the harness's own pull request, which
carries neither a body nor `data.heal` (also `heal-declined`). All three
express one rule: the harness does not heal a failure of work it filed for
itself (invariant 25).

A declined failure **stays in `failed/`** — that is the whole point of
declining it: no automated fix is coming, so it must keep reading as a problem
on the board rather than being retired to a terminal nobody watches. It is
claimed exactly once, to write one history line saying why the healer is
leaving it alone, and then written straight back; every later tick recognises
that line (`_already_declined`) and skips the candidate without claiming it, so
`failed/` neither churns nor loops.
"""

from __future__ import annotations

from dataclasses import replace

from harness.drivers.github_conflicts_check import SOURCE_KIND as MERGEABILITY_SOURCE_KIND
from harness.drivers.github_issues import MARKER_PREFIX
from harness.drivers.github_mergeable_check import SOURCE_KIND as PULL_REQUEST_SOURCE_KIND
from harness.models import END, FAILED, HistoryEntry, Task, append_history
from harness.ids import new_lock_id
from harness.ports.board import DONE_COLUMN, FAILED_COLUMN
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue
from harness.ports.triggers import Check, CheckSpec, Observation

ACTOR = "failed-tasks"

PR_BORN_SOURCE_KINDS = frozenset({MERGEABILITY_SOURCE_KIND, PULL_REQUEST_SOURCE_KIND})
"""`source.kind` values stamped by the two Checks that mint tasks from the
harness's own pull requests rather than a filed issue: `GithubConflictsCheck`
(`drivers/github_conflicts_check.py`) stamps its own `SOURCE_KIND`
(`"mergeability"`) on resolver tasks, `GithubMergeableCheck`
(`drivers/github_mergeable_check.py`) stamps its own `SOURCE_KIND`
(`"pull-request"`) on automerge-review tasks. Neither carries a body or
`data.heal`, so without this the one-hop limit (invariant 25) wouldn't cover
them. Built from those two drivers' own constants for the same reason
`MARKER_PREFIX` was extracted: these strings are owned by those two driver
files, and a silent rename there must not silently disarm the guard here."""

SPEC = CheckSpec(
    name="failed-tasks",
    label="Failed tasks",
    description=(
        "Hands each failed task to the self-heal workflow and retires the "
        "original into done. A failure it declines to heal stays in failed. "
        "Takes no settings."
    ),
)
"""The action definition for `failed-tasks`. It carries no parameters — but it
is a *fully declared* action, not an unknown one: wiring bundles this spec with
the factory (`app.build()`) so the UI renders it like any other action ("no
settings needed"), never as a raw-JSON blob."""


class FailedTasksCheck(Check):
    def __init__(
        self,
        *,
        failed: TaskQueue,
        done: TaskQueue,
        events: EventSink,
        clock: Clock,
        repository: str | None = None,
    ) -> None:
        self._failed = failed
        self._done = done
        self._events = events
        self._clock = clock
        self._repository = repository

    def evaluate(self) -> list[Observation]:
        observations: list[Observation] = []
        for candidate in self._failed.list():
            reason = _decline_reason(candidate)
            if reason is not None:
                # Declined: no heal task, and the failure stays in `failed/`
                # for the operator. Note it once, then leave it alone forever.
                self._decline(candidate, reason)
                continue
            task = self._failed.claim(candidate, new_lock_id())
            if task is None:
                continue  # lost the race to another evaluate() call — not an error
            self._events.emit(
                "healing", task_id=task.id, queue=FAILED, task=task.to_dict()
            )
            self._settle(task, "queued for healing")
            observations.append(self._observation(task))
        return observations

    def _observation(self, task: Task) -> Observation:
        data = {
            "request": _diagnostic_request(task),
            "body": _render_failure_report(task),
            "reason": _failure_reason(task),
            "history": _consumer_history(task),
            "original_request": _request_of(task),
            "heal": {"of": task.id},
        }
        source = task.data.get("source")
        if source is not None:
            data["source"] = source
        return Observation(state_key=task.id, data=data, repository=self._repository)

    def _settle(self, task: Task, note: str) -> None:
        """Retire a claimed failure into `done/`.

        `status=END` and the `done` column are the same terminal an ordinary
        completion reaches — deliberately, since from here on nobody has to do
        anything about this task. The `failed → end` move stamped by the
        `failed-tasks` actor is what distinguishes the two in the task's
        history, which is where the healer's involvement belongs (ADR-0024).
        """
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=FAILED,
            to_step=END,
            summary=note,
        )
        healed = append_history(replace(task, status=END, lock_id=None), entry)
        self._failed.transfer(healed, self._done)
        self._events.emit(
            "healed",
            task_id=task.id,
            queue=DONE_COLUMN,
            summary=note,
            task=healed.to_dict(),
        )

    def _decline(self, candidate: Task, note: str) -> None:
        """Record — exactly once — that this failure will not be healed.

        The task keeps its `failed` status and its place in `failed/`: nothing
        is coming to fix it, so it must stay where the operator looks. The
        history line is both the explanation and the marker that stops the next
        tick from claiming it again.
        """
        if _already_declined(candidate):
            return
        task = self._failed.claim(candidate, new_lock_id())
        if task is None:
            return  # lost the race — the next tick notes it instead
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=FAILED,
            to_step=FAILED,
            summary=note,
        )
        declined = append_history(replace(task, lock_id=None), entry)
        self._failed.transfer(declined, self._failed)
        self._events.emit(
            "heal-declined",
            task_id=task.id,
            queue=FAILED_COLUMN,
            summary=note,
            task=declined.to_dict(),
        )


def _decline_reason(task: Task) -> str | None:
    """Why this failure must not enter the heal pipeline, or `None` to heal it.

    Every guard reads only the task's own persisted `data`, so the decision is
    the same before a claim as after one — which is what lets `evaluate()` skip
    a declined candidate without touching the queue at all.
    """
    if task.data.get("heal") is not None:
        # Itself a `heal`-workflow task that failed. Healing it would be the
        # healer healing itself — the recursion guard.
        return "heal-failed: the heal attempt itself failed — left for the operator"
    if _descends_from_a_harness_filed_issue(task):
        # A fix task born from an issue the harness itself filed, which then
        # failed. Healing it again would file a fresh issue and feed the
        # pipeline its own output — the one-hop limit (invariant 25).
        return (
            "heal-declined: fix attempt for a harness-filed issue failed "
            "(one-hop limit) — left for the operator"
        )
    if _born_from_a_harness_pull_request(task):
        # A resolver or automerge-review task minted from the harness's own
        # open PR, which then failed. These carry neither a body nor
        # `data.heal`, so the two guards above miss them — same one-hop limit,
        # closed for the PR-borne half of the cycle (invariant 25).
        return (
            "heal-declined: fix attempt for harness-filed PR work failed "
            "(one-hop limit) — left for the operator"
        )
    return None


def _already_declined(task: Task) -> bool:
    """True once this check has written its decline note onto the task.

    The note itself is the marker — no extra `data` key — because the note is
    the only thing this check ever writes onto a task it leaves in `failed/`.
    A restart (`TaskControl.restart`) keeps the history, so a task that fails
    again is still recognised: the guards are structural, and the reason it was
    declined the first time has not changed.
    """
    return any(entry.actor == ACTOR for entry in task.history)


def _diagnostic_request(task: Task) -> str:
    """A short, synthesized diagnostic line — deliberately not the original
    task's own request (see `original_request`, carried separately)."""
    return (
        f"Diagnose why task {task.id} failed at step {task.status!r} "
        f"(workflow {task.workflow_template!r})."
    )


def _render_failure_report(task: Task) -> str:
    """The rendered failure report — what `compose_prompt` puts in
    `task.data["body"]` for the `heal` persona to actually read. `heal` runs
    through the generic `ClaudeCliBehavior`/`compose_prompt`, which has no
    concept of a structured failure report — without this rendered form the
    persona would receive no failure-report content in its prompt at all."""
    lines = [
        "## Failure report",
        f"- task id: {task.id}",
        f"- workflow: {task.workflow_template}",
        f"- failing step: {task.status or '(none)'}",
        f"- repository: {task.repository or '(none)'}",
    ]
    reason = _failure_reason(task)
    if reason:
        lines.append(f"- reason: {reason}")
    request = _request_of(task)
    if request:
        lines.append(f"- original request: {request}")

    steps = _consumer_history(task)
    if steps:
        lines.append("")
        lines.append("## What the task did before it failed")
        lines.extend(steps)

    lines += [
        "",
        "Decide whether this failure points at a fixable bug in the harness "
        "itself (a driver contract, a wiring gap, a missing workflow edge) or "
        "an operational/tuning problem worth filing (e.g. a step that ran out "
        "of its per-agent timeout) — as opposed to a genuinely external or "
        "transient failure (a flaky network) or a task whose own request was "
        "simply wrong or impossible.",
    ]
    return "\n".join(lines)


def _failure_reason(task: Task) -> str:
    """The reason from the last history entry that carries one (the failing move)."""
    for entry in reversed(task.history):
        if entry.reason:
            return entry.reason
    return ""


def _consumer_history(task: Task) -> list[str]:
    """One bullet per consumer step that recorded a summary."""
    lines: list[str] = []
    for entry in task.history:
        if entry.actor.startswith("consumer:") and entry.summary:
            lines.append(f"- **{entry.from_step}**: {entry.summary}")
    return lines


def _request_of(task: Task) -> str:
    for key in ("request", "title", "summary"):
        value = task.data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _descends_from_a_harness_filed_issue(task: Task) -> bool:
    """True when this failed task is a fix attempt for an issue the harness
    itself filed.

    `OpenIssueBehavior` embeds `<!-- harness-issue:<marker> -->` in every issue
    body it opens, and `GithubIssuesCheck` ingests that body verbatim into
    `data["body"]` — so the marker is provenance that survives the round trip
    through GitHub with no extra plumbing.

    The marker is deliberately generic: it covers the healer and every other
    `open-issue` consumer. That breadth is the point — the rule is that the
    harness does not heal a failure of work it filed for itself, which is the
    same runaway shape wherever it appears, and it is the cycle `data.heal`
    does not cover.

    This match is a heuristic, not proof of provenance: an issue body that
    merely quotes `<!-- harness-issue:` — say, in a bug report about this
    very check — is declined too. The direction is fail-safe (one lost
    diagnosis, never a loop), which is why a substring match is acceptable
    here.
    """
    body = task.data.get("body")
    return isinstance(body, str) and MARKER_PREFIX in body


def _born_from_a_harness_pull_request(task: Task) -> bool:
    """True when this failed task was minted by a Check that scans the
    harness's own open pull requests rather than ingesting a filed issue —
    a resolver task (`GithubConflictsCheck`) or an automerge-review task
    (`GithubMergeableCheck`). See `PR_BORN_SOURCE_KINDS` for the two
    `source.kind` values this recognises.

    Unlike `_descends_from_a_harness_filed_issue`, this is exact, not a
    heuristic: only the two Check drivers named above stamp these kinds.
    """
    source = task.data.get("source")
    return isinstance(source, dict) and source.get("kind") in PR_BORN_SOURCE_KINDS
