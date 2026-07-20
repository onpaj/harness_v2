"""Phase 2 dummy behavior: writes an artifact, commits the work, returns (outcome, summary).

The real work (agent) is slotted in here in phase 3 — by swapping the driver.
From the outside the contract does not change: it attaches to the worktree,
writes into the artifacts folder, commits.

Returns DONE deterministically. Optionally, for one step, it returns
REQUEST_CHANGES on the first pass so the workflow's back edge gets exercised —
but only once, otherwise the loop would spin forever.
"""

from __future__ import annotations

from harness.models import BehaviorResult, Outcome, Task
from harness.ports.artifacts import ArtifactStore
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.workspace import Workspace


class DummyBehavior(ConsumerBehavior):
    def __init__(
        self,
        *,
        clock: Clock,
        workspace: Workspace,
        artifacts: ArtifactStore,
        delay: float = 5.0,
        request_changes_once_at: str | None = None,
    ) -> None:
        self._clock = clock
        self._workspace = workspace
        self._artifacts = artifacts
        self._delay = delay
        self._step = request_changes_once_at
        self._already_asked: set[str] = set()

    async def run(self, task: Task) -> BehaviorResult:
        await self._clock.sleep(self._delay)
        step = task.status or ""

        asks_changes = (
            self._step is not None
            and step == self._step
            and task.id not in self._already_asked
        )
        if asks_changes:
            self._already_asked.add(task.id)
            outcome = Outcome.REQUEST_CHANGES
            summary = f"{step}: changes requested"
        else:
            outcome = Outcome.DONE
            summary = f"{step}: done"

        # Artifact into the harness folder (attempt-indexed), work into the
        # worktree, commit. The commit is done by this driver, not the consumer
        # or the LLM.
        slot = self._artifacts.begin(task.id, step)
        slot.put(f"{step}.md", f"# {step}\n\n{summary}\n")

        handle = self._workspace.attach(task)
        # Into `.artifacts/<task>/`, the versioned location the real agent uses
        # (invariant 16) — NOT `.harness/`, which repos routinely gitignore. A
        # dummy whose writes are ignored commits nothing, so the task branch has
        # no diff and landing cannot open a PR at all.
        handle.write(
            f".artifacts/{task.id}/{step}-{slot.attempt:02d}.md",
            f"# {step}\n\n{summary}\n",
        )
        handle.commit(f"[{step}] {summary}")

        return BehaviorResult(outcome, summary)
