"""`FailedTasksCheck`: the self-heal action, expressed as a process `Check`.

The inbound half of self-healing (ADR-0018), mirroring `GithubIssuesCheck`'s
shape: it claims work out of an existing queue (`failed/`) via the ordinary
atomic `claim()` — the same "idempotent, side-effecting claim action" a
`TaskSource.poll()` may perform (invariant #35) — and turns each claim into one
`Observation` that the compiled `ScheduledTrigger` fires as a fresh `heal`
task. `failed/` still drains monotonically: every claimed task settles onto
`healed/` in the same `evaluate()` call, success or failure, so a failure can
never be healed twice (invariant 25).

Unlike `GithubIssuesCheck`, this needs no external client — it needs the
harness's own live `failed`/`healed` queues and event sink, so it is
registered inside `app.build()` itself (§FR-6), not via a `cli.py` factory.

Recursion guard: a claimed task is declined — settled straight to `healed/`
with no `Observation` — in three cases. `data.heal` marks it as itself a
`heal` workflow task that failed (`heal-failed`, so a stuck healer can never
re-enter `failed/` a second time). `data["body"]` carrying the
`<!-- harness-issue:… -->` marker marks it as a fix attempt for an issue the
harness itself filed (`heal-declined`). `data["source"]["kind"]` being
`"mergeability"` or `"pull-request"` marks it as a resolver or
automerge-review task minted from the harness's own pull request, which
carries neither a body nor `data.heal` (also `heal-declined`). All three
express one rule: the harness does not heal a failure of work it filed for
itself (invariant 25).
"""

from __future__ import annotations

from dataclasses import replace

from harness.drivers.github_conflicts_check import SOURCE_KIND as MERGEABILITY_SOURCE_KIND
from harness.drivers.github_issues import MARKER_PREFIX
from harness.drivers.github_mergeable_check import SOURCE_KIND as PULL_REQUEST_SOURCE_KIND
from harness.models import FAILED, HEALED, HistoryEntry, Task, append_history
from harness.ids import new_lock_id
from harness.ports.board import HEALED_COLUMN
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
    description="Drains each failed task into the self-heal workflow. Takes no settings.",
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
        healed: TaskQueue,
        events: EventSink,
        clock: Clock,
        repository: str | None = None,
    ) -> None:
        self._failed = failed
        self._healed = healed
        self._events = events
        self._clock = clock
        self._repository = repository

    def evaluate(self) -> list[Observation]:
        observations: list[Observation] = []
        for candidate in self._failed.list():
            task = self._failed.claim(candidate, new_lock_id())
            if task is None:
                continue  # lost the race to another evaluate() call — not an error
            self._events.emit(
                "healing", task_id=task.id, queue=FAILED, task=task.to_dict()
            )
            if task.data.get("heal") is not None:
                # This claimed task is itself a `heal`-workflow task that
                # failed. Settle it, never re-observe it — the recursion guard.
                self._settle(task, "heal-failed: the heal attempt itself failed")
                continue
            if _descends_from_a_harness_filed_issue(task):
                # A fix task born from an issue the harness itself filed, which
                # then failed. Healing it again would file a fresh issue and
                # feed the pipeline its own output — the one-hop limit
                # (invariant 25).
                self._settle(
                    task,
                    "heal-declined: fix attempt for a harness-filed issue "
                    "failed (one-hop limit)",
                )
                continue
            if _born_from_a_harness_pull_request(task):
                # A resolver or automerge-review task minted from the
                # harness's own open PR, which then failed. These carry
                # neither a body nor `data.heal`, so the two guards above
                # miss them — same one-hop limit, closed for the PR-borne
                # half of the cycle (invariant 25).
                self._settle(
                    task,
                    "heal-declined: fix attempt for harness-filed PR work "
                    "failed (one-hop limit)",
                )
                continue
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
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=FAILED,
            to_step=HEALED,
            summary=note,
        )
        healed = append_history(replace(task, status=HEALED, lock_id=None), entry)
        self._failed.transfer(healed, self._healed)
        self._events.emit(
            "healed",
            task_id=task.id,
            queue=HEALED_COLUMN,
            summary=note,
            task=healed.to_dict(),
        )


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
