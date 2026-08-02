"""`MergePrBehavior` — the `merge-pr` finisher kind.

The worker half of "an agent reviews, the harness merges" — invariant 9's
philosophy (`the commit is done by the driver, not the LLM`) carried to its
most consequential case yet. The `merge-review` persona reads the diff and
writes a verdict; **this class decides**, by comparing that verdict's
confidence against a threshold the *operator* set in the workflow binding. The
agent never merges, and it never sets the bar it is judged against — a persona
that writes `"confidence": 1.0` on everything still cannot lower the gate.

Config (the binding's own, invariant #41):

- `from_step` names the step whose artifact holds the verdict. Its presence
  selects the *replace* shape (as in `open-issue`): the merge step runs no
  agent of its own, it only reads the review step's artifact and acts.
- `min_confidence` is the threshold, default `0.8`.
- `method` is the merge method (`merge`/`squash`/`rebase`), default `squash`.
- `dry_run` (default **True**) records the decision without merging. The
  default is the conservative one on purpose: configuring the Process is not
  the same as trusting it, so an operator watches it decide on real PRs for a
  while and flips one flag when the track record justifies it.

Everything the behavior needs about *which* PR comes from `task.data.source`,
stamped by `GithubMergeableCheck` — never from wiring, so one binding serves
every repo.

**Every failure path refuses the merge.** A missing source, an unreadable
verdict, a confidence below the bar, a head that moved: all settle `done` with
a summary saying why, or raise. None of them can merge by accident. That
asymmetry is deliberate — the cost of wrongly refusing is a human clicking a
button; the cost of wrongly merging is on the default branch.
"""

from __future__ import annotations

from typing import Callable

from harness.merge_verdict import VerdictError, parse_verdict
from harness.models import DONE, BehaviorResult, Task
from harness.ports.artifacts import ArtifactView
from harness.ports.behavior import ConsumerBehavior
from harness.ports.pr_merge import MergeError, MergeRefused, PullRequestMerger

DEFAULT_MIN_CONFIDENCE = 0.8
DEFAULT_METHOD = "squash"


class MergePrBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        merger: PullRequestMerger,
        artifacts: ArtifactView,
        from_step: str | None = None,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        method: str = DEFAULT_METHOD,
        dry_run: bool = True,
        inner: ConsumerBehavior | None = None,
        body_for: Callable[[Task, object], str] | None = None,
    ) -> None:
        self._merger = merger
        self._artifacts = artifacts
        self._from_step = from_step
        self._min_confidence = min_confidence
        self._method = method
        self._dry_run = dry_run
        self._inner = inner
        self._body_for = body_for or _default_body

    async def run(self, task: Task) -> BehaviorResult:
        inner_result = None
        if self._inner is not None:
            inner_result = await self._inner.run(task)
        outcome = inner_result.outcome if inner_result is not None else DONE

        source = task.data.get("source")
        if not isinstance(source, dict) or source.get("kind") != "pull-request":
            return BehaviorResult(
                outcome,
                "merge refused: the task carries no pull-request source",
            )

        repo, number = source.get("repo"), source.get("pr")
        if not isinstance(repo, str) or not isinstance(number, int):
            return BehaviorResult(
                outcome, "merge refused: the task's source names no repo/PR"
            )

        step = self._from_step or task.status or ""
        # A VerdictError is a real fault — the persona routed here (it approved)
        # but wrote nothing readable to justify it. Fail loudly rather than
        # silently never merging; nothing has merged at this point.
        try:
            verdict = parse_verdict(self._latest_artifact(task.id, step))
        except VerdictError as error:
            raise MergeError(f"step {step!r} of task {task.id}: {error}") from None

        if verdict.confidence < self._min_confidence:
            return BehaviorResult(
                outcome,
                f"merge refused: confidence {verdict.confidence:.2f} is below the "
                f"{self._min_confidence:.2f} threshold"
                + (f" — {verdict.reasoning}" if verdict.reasoning else ""),
            )

        head_sha = source.get("head_sha")
        if not isinstance(head_sha, str) or not head_sha:
            return BehaviorResult(
                outcome,
                "merge refused: the task's source carries no head sha to pin the "
                "merge to, so the reviewed code cannot be proven current",
            )

        if self._dry_run:
            return BehaviorResult(
                outcome,
                f"merge withheld (dry run): would {self._method}-merge {repo}#{number} "
                f"at {head_sha[:7]} with confidence {verdict.confidence:.2f}",
                data={"merge": {"dryRun": True, "confidence": verdict.confidence}},
            )

        title = source.get("pr_title") or f"Merge pull request #{number}"
        try:
            ref = self._merger.merge(
                repo,
                number,
                method=self._method,
                expected_sha=head_sha,
                title=str(title),
                body=self._body_for(task, verdict),
            )
        except MergeRefused as error:
            # A state of the world, not a fault: the next scan sees the new
            # head as a new state and reviews it from scratch.
            return BehaviorResult(outcome, f"merge refused by the forge: {error}")

        return BehaviorResult(
            outcome,
            f"merged {repo}#{number} ({self._method}) at {head_sha[:7]} with "
            f"confidence {verdict.confidence:.2f}",
            data={
                "merge": {
                    "repo": repo,
                    "number": number,
                    "sha": ref.sha,
                    "method": self._method,
                    "confidence": verdict.confidence,
                }
            },
        )

    def _latest_artifact(self, task_id: str, step: str) -> str:
        """The step's highest-attempt artifact, or "" when it wrote none."""
        refs = [ref for ref in self._artifacts.list(task_id) if ref.step == step]
        if not refs:
            return ""
        latest = max(refs, key=lambda ref: ref.attempt)
        return self._artifacts.read(task_id, step, latest.attempt, latest.name) or ""


def _default_body(task: Task, verdict) -> str:
    """The merge commit body: why the harness merged this, in the git history.

    A merge nobody can trace back to a decision is worse than no automation, so
    the confidence, the reasoning and any noted risks ride into the commit —
    the same instinct as landing putting the step summary in the PR body.
    """
    lines = [
        f"Merged automatically by the harness (task {task.id}).",
        "",
        f"Confidence: {verdict.confidence:.2f}",
    ]
    if verdict.reasoning:
        lines += ["", verdict.reasoning]
    if verdict.risks:
        lines += ["", "Noted risks:"] + [f"- {risk}" for risk in verdict.risks]
    return "\n".join(lines)
