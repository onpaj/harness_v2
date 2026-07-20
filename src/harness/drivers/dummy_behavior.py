"""Dummy behavior fáze 2: napíše artefakt, commitne práci, vrátí (outcome, summary).

Skutečná práce (agent) se sem vloží ve fázi 3 — záměnou driveru. Zvenčí se
kontrakt nezmění: připojí se k worktree, píše do složky artefaktů, commituje.

Vrací DONE deterministicky. Volitelně pro jeden krok vrátí při prvním průchodu
REQUEST_CHANGES, aby se zpětná hrana workflow proklepla — ale jen jednou, jinak
by se smyčka točila donekonečna.
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
            summary = f"{step}: vyžádány změny"
        else:
            outcome = Outcome.DONE
            summary = f"{step}: hotovo"

        # Artefakt do harnessové složky (attempt-indexed), práce do worktree,
        # commit. Commit dělá tenhle driver, ne consumer ani LLM.
        slot = self._artifacts.begin(task.id, step)
        slot.put(f"{step}.md", f"# {step}\n\n{summary}\n")

        handle = self._workspace.attach(task)
        handle.write(f".harness/progress/{step}.{slot.attempt}.txt", f"{summary}\n")
        handle.commit(f"[{step}] {summary}")

        return BehaviorResult(outcome, summary)
