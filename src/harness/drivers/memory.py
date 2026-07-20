"""In-memory drivery. Používají se v testech — bez disku a bez čekání."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from harness.models import BehaviorResult, Outcome, Task, Workflow
from harness.ports.artifacts import (
    ArtifactRef,
    ArtifactSlot,
    ArtifactStore,
)
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.forge import Forge, PullRequest
from harness.ports.queue import TaskQueue
from harness.ports.workflows import WorkflowNotFound, WorkflowRepository
from harness.ports.workspace import Workspace, WorkspaceHandle


class MemoryTaskQueue(TaskQueue):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self._ready: dict[str, Task] = {}
        self._claimed: dict[str, Task] = {}

    def list(self) -> list[Task]:
        return list(self._ready.values())

    def claim(self, task: Task, lock_id: str) -> Task | None:
        if task.id not in self._ready:
            return None
        del self._ready[task.id]
        claimed = replace(task, lock_id=lock_id)
        self._claimed[task.id] = claimed
        return claimed

    def put(self, task: Task) -> None:
        self._ready[task.id] = task

    def transfer(self, task: Task, destination: TaskQueue) -> None:
        self._claimed.pop(task.id, None)
        destination.put(task)

    def recover(self) -> int:
        count = len(self._claimed)
        for task_id, task in self._claimed.items():
            self._ready[task_id] = replace(task, lock_id=None)
        self._claimed.clear()
        return count


class MemoryWorkflowRepository(WorkflowRepository):
    def __init__(self, workflows: dict[str, Workflow]) -> None:
        self._workflows = workflows

    def get(self, name: str) -> Workflow:
        try:
            return self._workflows[name]
        except KeyError:
            raise WorkflowNotFound(f"workflow {name!r} neexistuje") from None


class MemoryEventSink(EventSink):
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, name: str, **fields: Any) -> None:
        self.events.append((name, fields))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


class FakeClock(Clock):
    def __init__(self, instant: str = "2026-07-19T10:00:00Z") -> None:
        self.instant = instant
        self.slept: list[float] = []

    def now(self) -> str:
        return self.instant

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


class ScriptedBehavior(ConsumerBehavior):
    """Vrací předepsané outcomes podle kroku, na kterém task stojí.

    Dojdou-li předpisy pro daný krok, vrací DONE.
    """

    def __init__(self, outcomes: dict[str, list[Outcome]] | None = None) -> None:
        self._outcomes = {step: list(values) for step, values in (outcomes or {}).items()}
        self.seen: list[str] = []

    async def run(self, task: Task) -> BehaviorResult:
        step = task.status or ""
        self.seen.append(step)
        pending = self._outcomes.get(step)
        outcome = pending.pop(0) if pending else Outcome.DONE
        return BehaviorResult(outcome, summary=f"{step}: {outcome.value}")


class MemoryArtifactSlot(ArtifactSlot):
    def __init__(self, store: "MemoryArtifactStore", task_id: str, step: str, attempt: int) -> None:
        self._store = store
        self._task_id = task_id
        self._step = step
        self._attempt = attempt

    @property
    def attempt(self) -> int:
        return self._attempt

    def put(self, name: str, content: str) -> None:
        self._store._data[(self._task_id, self._step, self._attempt, name)] = content


class MemoryArtifactStore(ArtifactStore):
    """Artefakty v dictu. `begin` alokuje další attempt pro (task, step)."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str, int, str], str] = {}
        self._next: dict[tuple[str, str], int] = {}

    def begin(self, task_id: str, step: str) -> MemoryArtifactSlot:
        attempt = self._next.get((task_id, step), 0)
        self._next[(task_id, step)] = attempt + 1
        return MemoryArtifactSlot(self, task_id, step, attempt)

    def list(self, task_id: str) -> tuple[ArtifactRef, ...]:
        refs = [
            ArtifactRef(step, attempt, name)
            for (tid, step, attempt, name) in self._data
            if tid == task_id
        ]
        return tuple(sorted(refs, key=lambda ref: (ref.step, ref.attempt, ref.name)))

    def read(self, task_id: str, step: str, attempt: int, name: str) -> str | None:
        return self._data.get((task_id, step, attempt, name))


class MemoryWorkspaceHandle(WorkspaceHandle):
    def __init__(self, task_id: str) -> None:
        self._branch = f"harness/{task_id}"
        self._path = Path("/memory/worktrees") / task_id
        self.writes: list[tuple[str, str]] = []
        self.commits: list[str] = []

    @property
    def path(self) -> Path:
        return self._path

    @property
    def branch(self) -> str:
        return self._branch

    def write(self, relpath: str, content: str) -> None:
        self.writes.append((relpath, content))

    def commit(self, message: str) -> str | None:
        self.commits.append(message)
        return f"sha{len(self.commits)}"


class MemoryWorkspace(Workspace):
    """Worktree v paměti. Opětovný attach téhož tasku vrací týž handle."""

    def __init__(self) -> None:
        self.handles: dict[str, MemoryWorkspaceHandle] = {}

    def attach(self, task: Task) -> MemoryWorkspaceHandle:
        handle = self.handles.get(task.id)
        if handle is None:
            handle = MemoryWorkspaceHandle(task.id)
            self.handles[task.id] = handle
        return handle


class MemoryForge(Forge):
    """Zaznamenává PR. Idempotentní podle branch."""

    def __init__(self) -> None:
        self.opened: list[PullRequest] = []
        self.bodies: dict[str, str] = {}

    def open_pull_request(
        self, task: Task, *, branch: str, title: str, body: str
    ) -> PullRequest:
        for pull in self.opened:
            if pull.branch == branch:
                return pull
        pull = PullRequest(
            number=len(self.opened) + 1,
            url=f"https://forge.local/pr/{len(self.opened) + 1}",
            branch=branch,
            title=title,
        )
        self.opened.append(pull)
        self.bodies[branch] = body
        return pull
