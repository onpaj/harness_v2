"""`OpenIssueBehavior` — the `open-issue` finisher kind (ADR-0016, ADR-0018).

The generic, second half of self-healing's outbound leg: the `heal` step's
persona only drafts an issue and returns a verdict (invariants 9/26 —
never opens anything itself); this finisher — a `ConsumerBehavior`, resolved
through the finisher registry exactly like `LandingBehavior`/`"open-pr"` — is
the worker that reads the draft and actually calls `IssueTracker.open_issue`.
Deliberately generic: any future process whose target's last step drafts an
issue can bind to this same kind.

No error handling of its own: `Consumer.tick()` already wraps `behavior.run()`
in a blanket `except Exception` -> `_fail`, so an `IssueError` here lands the
task in `failed/` exactly like an agent exception does. `FailedTasksCheck`'s
recursion guard (keyed on `task.data["heal"]["of"]`) is what stops that from
looping — not in-behavior error handling.
"""

from __future__ import annotations

from harness.models import DONE, BehaviorResult, Task
from harness.ports.artifacts import ArtifactView
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.issues import IssueTracker


class OpenIssueBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        tracker: IssueTracker,
        repo: str,
        artifacts: ArtifactView,
        clock: Clock,
        labels: tuple[str, ...] = ("harness:self-heal",),
    ) -> None:
        self._tracker = tracker
        self._repo = repo
        self._artifacts = artifacts
        self._clock = clock
        self._labels = labels

    async def run(self, task: Task) -> BehaviorResult:
        draft = self._latest_draft(task.id)
        title = _title(task, draft)
        body = _body(task, draft)
        marker = task.data["heal"]["of"]

        ref = self._tracker.open_issue(
            self._repo, title=title, body=body, labels=self._labels, marker=marker
        )
        return BehaviorResult(DONE, f"opened issue {ref.url}")

    def _latest_draft(self, task_id: str) -> str:
        """The `heal` step's drafted artifact — the highest attempt, mirroring
        the general latest-attempt convention (in practice a single candidate
        for a fresh heal task)."""
        refs = [ref for ref in self._artifacts.list(task_id) if ref.step == "heal"]
        if not refs:
            return ""
        latest = max(refs, key=lambda ref: ref.attempt)
        content = self._artifacts.read(task_id, "heal", latest.attempt, latest.name)
        return content or ""


def _heal_verdict_summary(task: Task) -> str | None:
    """The `heal` step's verdict summary, recovered from history.

    `OpenIssueBehavior.run(task)` has no live `AgentRun` in scope (unlike the
    old `Healer`, which called the runner directly) — but the summary isn't
    lost: `Consumer._deliver` already appended it to `task.history` as a
    `consumer:heal` entry the moment the `heal` step returned `done`, and that
    history rides the task through the router into `file-issue`."""
    for entry in reversed(task.history):
        if entry.from_step == "heal" and entry.actor.startswith("consumer:"):
            return entry.summary
    return None


def _title(task: Task, draft: str) -> str:
    """Issue title: the first `# ` heading of the draft, else the verdict
    summary, else the original request, else a generic line."""
    for raw in draft.splitlines():
        line = raw.strip()
        if line.startswith("# "):
            return line[2:].strip()
    summary = _heal_verdict_summary(task)
    if summary:
        return summary
    request = task.data.get("original_request")
    if isinstance(request, str) and request.strip():
        return f"Self-heal: {request.strip()}"
    return f"Self-heal: task {task.data['heal']['of']} failed"


def _body(task: Task, draft: str) -> str:
    """Issue body: the agent's draft plus a footer linking the origin when known."""
    of = task.data["heal"]["of"]
    parts = [draft.strip() or f"The harness task {of} failed."]
    source = task.data.get("source")
    if isinstance(source, dict):
        url = source.get("url")
        issue = source.get("issue")
        if url:
            parts.append(f"\nOrigin: {url}")
        elif issue:
            parts.append(f"\nOrigin issue: {issue}")
    parts.append(f"\n_Filed by the harness healer for failed task `{of}`._")
    return "\n".join(parts)
