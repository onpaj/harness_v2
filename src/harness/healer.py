"""Healer: the agent assigned to the `failed/` queue.

A core loop, sibling of `SourcePoller` — it knows only ports (`TaskQueue`,
`AgentRunner`, `IssueTracker`, `EnqueueStrategy`, `EventSink`, `Clock`), never a
driver. Wiring lives in `app.py`.

It claims a failed task, runs the `healer` persona over a **failure report**
(the task's reason + history — no worktree, no git), and if the agent judges the
failure a fixable harness bug, opens a GitHub issue on the harness repo via the
`IssueTracker`. Then it settles the task onto the terminal `healed/` queue.

Two properties hold by construction (invariants 24, 25):

- **`failed/` drains monotonically.** A task is claimed out of `failed/` exactly
  once (the atomic `claim`) and settled to `healed/` — success *or* failure. It
  is never written back to `failed/`, so no failure can be healed twice.
- **The healer produces an issue, never a task.** Nothing it does can re-enter
  `failed/`, so there is no recursion to guard: even the healer's own errors
  settle to `healed/` with a `heal-failed` note.

The agent only drafts (writes `issue.md`, returns a verdict); the loop is the
worker that opens the issue (invariant 9, 26).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from harness.ids import new_lock_id
from harness.models import (
    DONE,
    FAILED,
    HEALED,
    HistoryEntry,
    Task,
    append_history,
)
from harness.ports.agent import AgentRunner, AgentSpec
from harness.ports.board import HEALED_COLUMN
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.issues import IssueError, IssueTracker
from harness.ports.queue import TaskQueue
from harness.ports.strategy import EnqueueStrategy

ACTOR = "healer"


class Healer:
    def __init__(
        self,
        *,
        failed: TaskQueue,
        healed: TaskQueue,
        runner: AgentRunner,
        spec: AgentSpec,
        tracker: IssueTracker,
        repo: str,
        scratch_root: Path,
        strategy: EnqueueStrategy,
        events: EventSink,
        clock: Clock,
        labels: tuple[str, ...] = ("harness:self-heal",),
        timeout: float = 1800.0,
    ) -> None:
        self._failed = failed
        self._healed = healed
        self._runner = runner
        self._spec = spec
        self._tracker = tracker
        self._repo = repo
        self._scratch_root = Path(scratch_root)
        self._strategy = strategy
        self._events = events
        self._clock = clock
        self._labels = labels
        self._timeout = timeout

    async def tick(self) -> bool:
        """Heal at most one failed task. True when something was processed."""
        selected = self._strategy.select(self._failed.list())
        if selected is None:
            return False

        task = self._failed.claim(selected, new_lock_id())
        if task is None:
            # Lost the race for this task to another tick — not an error.
            return False

        self._events.emit(
            "healing", task_id=task.id, queue=FAILED, task=task.to_dict()
        )
        note = await self._heal(task)
        self._settle(task, note)
        return True

    async def _heal(self, task: Task) -> str:
        """Run the agent and, on a `done` verdict, file the issue. Returns the
        settle note. Never raises — every failure path becomes a `heal-failed`
        note so the task still settles to `healed/`."""
        scratch = self._scratch_root / task.id
        scratch.mkdir(parents=True, exist_ok=True)
        prompt = heal_prompt(task, spec=self._spec)

        try:
            run = await self._runner.run(
                prompt=prompt, spec=self._spec, cwd=scratch, timeout=self._timeout
            )
        except Exception as error:  # noqa: BLE001 - a heal failure must not loop
            self._events.emit("heal_error", task_id=task.id, error=str(error))
            return f"heal-failed: agent error: {error}"

        if run.outcome != DONE:
            # The agent found nothing actionable for the harness — settle cleanly.
            return f"no action: {run.summary}" if run.summary else "no action"

        body = _read_draft(scratch)
        title = _title(run, task, body)
        try:
            ref = self._tracker.open_issue(
                self._repo,
                title=title,
                body=_body(task, body),
                labels=self._labels,
                marker=task.id,
            )
        except IssueError as error:
            self._events.emit("heal_error", task_id=task.id, error=str(error))
            return f"heal-failed: {error}"
        return f"opened issue {ref.url}"

    def _settle(self, task: Task, note: str) -> None:
        entry = HistoryEntry(
            at=self._clock.now(),
            actor=ACTOR,
            from_step=FAILED,
            to_step=HEALED,
            summary=note,
        )
        healed = append_history(
            replace(task, status=HEALED, lock_id=None), entry
        )
        self._failed.transfer(healed, self._healed)
        self._events.emit(
            "healed",
            task_id=task.id,
            queue=HEALED_COLUMN,
            summary=note,
            task=healed.to_dict(),
        )


def heal_prompt(task: Task, *, spec: AgentSpec) -> str:
    """The user prompt for the healer agent — a failure report built from the task.

    Unlike `compose_prompt`, it is worktree/artifact-free: the healer works from
    the failure itself. The persona (how to decide, that it must write `issue.md`
    and close with a verdict) lives in `spec.prompt`; here we supply the facts.
    """
    allowed = ", ".join(spec.allowed_outcomes)
    lines = [
        f"A harness task failed. You are the healer for it (task {task.id}).",
        "",
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
        "itself (a driver contract, a wiring gap, a missing workflow edge) — as "
        "opposed to an external or expected failure (a flaky network, a task "
        "that was simply wrong).",
        "",
        "If it is a fixable harness bug: write a proposed GitHub issue to the "
        "file `issue.md` in your working directory — a first line `# <title>`, "
        "then a diagnosis and a concrete proposed change — and finish with the "
        'verdict `{"outcome": "done", ...}`.',
        "If there is nothing actionable for the harness: finish with the verdict "
        '`{"outcome": "request_changes", ...}` and do not write a file.',
        "",
        "The harness reads your result by machine. Your final message MUST end "
        "with exactly this fenced verdict block and nothing after it:",
        "```json",
        '{"outcome": "<one of: ' + allowed + '>", "summary": "<short summary>"}',
        "```",
    ]
    return "\n".join(lines)


def _read_draft(scratch: Path) -> str:
    """The agent's drafted issue body, or an empty string when it wrote nothing."""
    draft = scratch / "issue.md"
    try:
        return draft.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


def _title(run, task: Task, body: str) -> str:
    """Issue title: the first `# ` heading of the draft, else the verdict summary,
    else the original request, else a generic line."""
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("# "):
            return line[2:].strip()
    if run.summary:
        return run.summary
    request = _request_of(task)
    if request:
        return f"Self-heal: {request}"
    return f"Self-heal: task {task.id} failed"


def _body(task: Task, draft: str) -> str:
    """Issue body: the agent's draft plus a footer linking the origin when known."""
    parts = [draft.strip() or f"The harness task {task.id} failed."]
    source = task.data.get("source")
    if isinstance(source, dict):
        url = source.get("url")
        issue = source.get("issue")
        if url:
            parts.append(f"\nOrigin: {url}")
        elif issue:
            parts.append(f"\nOrigin issue: {issue}")
    parts.append(f"\n_Filed by the harness healer for failed task `{task.id}`._")
    return "\n".join(parts)


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
