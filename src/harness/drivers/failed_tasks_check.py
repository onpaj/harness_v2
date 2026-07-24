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

Recursion guard: a claimed task carrying `data.heal` is itself a `heal`
workflow task that failed — it is settled straight to `healed/` with a
`heal-failed` note and yields no `Observation`, so a stuck healer can never
re-enter `failed/` a second time (invariant 25).
"""

from __future__ import annotations

from dataclasses import replace

from harness.models import FAILED, HEALED, HistoryEntry, Task, append_history
from harness.ids import new_lock_id
from harness.ports.board import HEALED_COLUMN
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.queue import TaskQueue
from harness.ports.triggers import Check, CheckSpec, Observation

ACTOR = "failed-tasks"

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
