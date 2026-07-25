"""`OpenIssueBehavior` — the `open-issue` finisher kind (ADR-0016, ADR-0018).

The worker half of "an agent drafts, the harness files" (invariants 9/26): a
step's persona writes issue drafts into its artifact and returns a verdict;
this finisher reads them and calls `IssueTracker.open_issue`. The persona
never opens anything itself.

Everything about *which* issues, on *which* repo, under *which* label is the
binding's `config` — this class has no notion of healing, reviewing, or any
other purpose:

- `from_step` names the step whose artifact holds the drafts. Its **presence
  selects the shape**: given, the finisher fully *replaces* the bound step's
  behavior (the healer's agent-less `file-issue` step); omitted, it *wraps*
  the step's own behavior — `inner` runs first, then its artifact is filed.
- `label` is carried by every issue and is the scope the idempotency search
  reads.
- `allowed_labels` is the allowlist a draft's own labels are filtered against,
  so a hallucinated label cannot 422 the whole step.

The repository is derived from the *task*, not from wiring: `slug_for` is an
injected callable (`task.repository` → `owner/repo`). It is injected rather
than imported because resolving a slug reads a clone's git remote — a driver —
and `behaviors/` may not import `drivers/` (`test_architecture.py`).

No error handling of its own: `Consumer.tick()` already wraps `behavior.run()`
in a blanket `except Exception` -> `_fail`, so an `IssueError` here lands the
task in `failed/` exactly like an agent exception does.
"""

from __future__ import annotations

from typing import Callable

from harness.issue_drafts import DraftError, marker_for, parse_drafts
from harness.models import DONE, BehaviorResult, Task
from harness.ports.artifacts import ArtifactView
from harness.ports.behavior import ConsumerBehavior
from harness.ports.issues import IssueError, IssueTracker


class OpenIssueBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        tracker: IssueTracker,
        artifacts: ArtifactView,
        slug_for: Callable[[str | None], str],
        label: str,
        from_step: str | None = None,
        allowed_labels: tuple[str, ...] = (),
        inner: ConsumerBehavior | None = None,
    ) -> None:
        self._tracker = tracker
        self._artifacts = artifacts
        self._slug_for = slug_for
        self._label = label
        self._from_step = from_step
        self._allowed_labels = allowed_labels
        self._inner = inner

    async def run(self, task: Task) -> BehaviorResult:
        inner_result = None
        if self._inner is not None:
            inner_result = await self._inner.run(task)

        step = self._from_step or task.status or ""
        repo = self._slug_for(task.repository)

        try:
            drafts = parse_drafts(self._latest_artifact(task.id, step))
        except DraftError as error:
            raise IssueError(f"step {step!r} of task {task.id}: {error}") from None

        refs = []
        dropped: list[str] = []
        for draft in drafts:
            allowed = tuple(
                label for label in draft.labels if label in self._allowed_labels
            )
            dropped.extend(
                label for label in draft.labels if label not in self._allowed_labels
            )
            refs.append(
                self._tracker.open_issue(
                    repo,
                    title=draft.title,
                    body=draft.body,
                    labels=allowed,
                    marker=marker_for(task.id, draft.title),
                    scope_label=self._label,
                )
            )

        outcome = inner_result.outcome if inner_result is not None else DONE
        return BehaviorResult(outcome, _summary(refs, dropped))

    def _latest_artifact(self, task_id: str, step: str) -> str:
        """The step's highest-attempt artifact, or "" when it wrote none."""
        refs = [ref for ref in self._artifacts.list(task_id) if ref.step == step]
        if not refs:
            return ""
        latest = max(refs, key=lambda ref: ref.attempt)
        return self._artifacts.read(task_id, step, latest.attempt, latest.name) or ""


def _summary(refs, dropped: list[str]) -> str:
    if not refs:
        summary = "no issues to file"
    else:
        numbers = ", ".join(f"#{ref.number}" for ref in refs)
        noun = "issue" if len(refs) == 1 else "issues"
        summary = f"opened {len(refs)} {noun}: {numbers}"
    if dropped:
        unique = ", ".join(sorted(set(dropped)))
        summary = f"{summary} (dropped labels outside the allowlist: {unique})"
    return summary
