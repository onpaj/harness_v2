"""In-memory drivers. Used in tests — no disk and no waiting."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from harness.ids import new_task_id
from harness.models import BehaviorResult, Outcome, Task, Workflow
from harness.ports.agent import (
    AgentCatalog,
    AgentNotFound,
    AgentRun,
    AgentRunner,
    AgentSpec,
)
from harness.ports.artifacts import (
    ArtifactRef,
    ArtifactSlot,
    ArtifactStore,
)
from harness.ports.behavior import ConsumerBehavior
from harness.ports.clock import Clock
from harness.ports.events import EventSink
from harness.ports.forge import Forge, PullRequest
from harness.ports.issues import IssueRef, IssueTracker
from harness.ports.merge import MergeChecker
from harness.ports.queue import TaskQueue
from harness.ports.repos import RepositoryNotFound, RepositoryRegistry
from harness.ports.source import FinishResult, Progress, TaskSource, dedup_key
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
            raise WorkflowNotFound(f"workflow {name!r} does not exist") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._workflows))


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
    """Returns scripted outcomes based on the step the task is on.

    When the scripted outcomes for a step run out, returns DONE.
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
    """Artifacts in a dict. `begin` allocates the next attempt for (task, step)."""

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
    def __init__(self, task_id: str, *, branch: str | None = None) -> None:
        self._branch = branch or f"harness/{task_id}"
        self._path = Path("/memory/worktrees") / task_id
        self.writes: list[tuple[str, str]] = []
        self.commits: list[str] = []
        self.pushes: list[str] = []
        # Test seam for ResolveConflictBehavior: preset whether the next
        # merge() call should report a conflict.
        self.conflicted: bool = False
        self.merges: list[str] = []

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

    def push(self) -> None:
        self.pushes.append(self._branch)

    def merge(self, base: str) -> bool:
        self.merges.append(base)
        return self.conflicted


class MemoryWorkspace(Workspace):
    """Worktree in memory. Re-attaching the same task returns the same handle."""

    def __init__(self) -> None:
        self.handles: dict[str, MemoryWorkspaceHandle] = {}

    def attach(self, task: Task) -> MemoryWorkspaceHandle:
        handle = self.handles.get(task.id)
        if handle is None:
            handle = MemoryWorkspaceHandle(task.id, branch=task.data.get("branch"))
            self.handles[task.id] = handle
        return handle


class MemoryTaskSource(TaskSource):
    """In-memory task source. An "issue" is a row in an internal queue, and
    "claim" moves it out of the unconsumed set — the twin of the GitHub
    adapter's `poll()` without the network. Outbound projection
    (`report_progress`/`finish`) is written into `states` for assertions.
    """

    kind = "memory"

    def __init__(
        self,
        *,
        clock: Clock,
        workflow: str = "default",
        repository: str | None = None,
        worktree_root: str = "/memory/worktrees",
    ) -> None:
        self._clock = clock
        self._workflow = workflow
        self._repository = repository
        self._worktree_root = worktree_root
        self._pending: list[tuple[str, str, str]] = []  # (issue_id, title, body)
        self._next_issue = 0
        self.states: dict[str, list] = {}

    def submit(self, title: str, body: str = "") -> str:
        """Test helper: add an "issue" to the queue and return its id."""
        issue_id = f"issue-{self._next_issue}"
        self._next_issue += 1
        self._pending.append((issue_id, title, body))
        return issue_id

    def poll(self) -> list[Task]:
        claimed = self._pending
        self._pending = []
        tasks: list[Task] = []
        for issue_id, title, body in claimed:
            task_id = new_task_id()
            tasks.append(
                Task(
                    id=task_id,
                    workflow_template=self._workflow,
                    created=self._clock.now(),
                    repository=self._repository,
                    worktree=f"{self._worktree_root}/{task_id}",
                    dedup_key=dedup_key(self.kind, issue_id),
                    data={
                        "title": title,
                        "body": body,
                        "source": {"kind": self.kind, "issue": issue_id},
                    },
                )
            )
        return tasks

    def report_progress(self, task: Task, progress: Progress) -> None:
        if not self._mine(task):
            return
        self.states.setdefault(self._issue(task), []).append(
            ("progress", progress.step)
        )

    def finish(self, task: Task, result: FinishResult) -> None:
        if not self._mine(task):
            return
        self.states.setdefault(self._issue(task), []).append(("finish", result.ok))

    def _mine(self, task: Task) -> bool:
        return task.data.get("source", {}).get("kind") == self.kind

    def _issue(self, task: Task) -> str:
        return task.data["source"]["issue"]


class MemoryForge(Forge):
    """Records PRs. Idempotent by branch."""

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
            repo=f"memory/{branch}",
        )
        self.opened.append(pull)
        self.bodies[branch] = body
        return pull


class FakeMergeChecker(MergeChecker):
    """Test double: direct control over merge state, no `GithubClient` needed."""

    def __init__(self) -> None:
        self.merged: set[tuple[str, int]] = set()
        self.raises: set[tuple[str, int]] = set()

    def is_merged(self, task: Task) -> bool | None:
        pr = task.data.get("pr")
        if not isinstance(pr, dict):
            return None
        key = (pr.get("repo"), pr.get("number"))
        if key in self.raises:
            raise RuntimeError(f"merge check failed for {key}")
        return key in self.merged


class MemoryIssueTracker(IssueTracker):
    """Records opened issues in a list. Idempotent by marker — the twin of
    `MemoryForge`'s idempotency by branch. For unit/e2e/smoke, no network."""

    def __init__(self) -> None:
        self.opened: list[dict[str, Any]] = []

    def open_issue(
        self,
        repo: str,
        *,
        title: str,
        body: str,
        labels: tuple[str, ...],
        marker: str,
    ) -> IssueRef:
        for existing in self.opened:
            if existing["repo"] == repo and existing["marker"] == marker:
                return existing["ref"]
        number = len(self.opened) + 1
        ref = IssueRef(number=number, url=f"https://forge.local/{repo}/issues/{number}")
        self.opened.append(
            {
                "repo": repo,
                "title": title,
                "body": body,
                "labels": labels,
                "marker": marker,
                "ref": ref,
            }
        )
        return ref


class MemoryAgentCatalog(AgentCatalog):
    """Catalog over a name → spec dict."""

    def __init__(self, specs: dict[str, AgentSpec]) -> None:
        self._specs = specs

    def get(self, name: str) -> AgentSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise AgentNotFound(f"agent {name!r} does not exist") from None


class FakeAgentRunner(AgentRunner):
    """Scripted runner without a subprocess.

    `runs` maps an agent name to its verdict; `default` is the fallback when
    the name is not in `runs`. `writes` maps an agent name to files (relpath →
    content) that are written into `cwd` during the run — simulating an agent
    writing artifacts and code. `outputs` maps an agent name to lines it "streams"
    through `on_output` during the run — simulating live stage output. Every call
    is recorded in `self.calls`.
    """

    def __init__(
        self,
        runs: dict[str, AgentRun] | None = None,
        default: AgentRun | None = None,
        writes: dict[str, dict[str, str]] | None = None,
        outputs: dict[str, list[str]] | None = None,
    ) -> None:
        self._runs = runs or {}
        self._default = default
        self._writes = writes or {}
        self._outputs = outputs or {}
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        *,
        prompt: str,
        spec: AgentSpec,
        cwd: Path,
        timeout: float,
        on_output: Callable[[str], None] | None = None,
    ) -> AgentRun:
        self.calls.append(
            {"prompt": prompt, "spec": spec, "cwd": cwd, "timeout": timeout}
        )
        if on_output is not None:
            for line in self._outputs.get(spec.name, ()):
                on_output(line)
        for relpath, content in self._writes.get(spec.name, {}).items():
            target = Path(cwd) / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        if spec.name in self._runs:
            return self._runs[spec.name]
        if self._default is not None:
            return self._default
        return AgentRun(Outcome.DONE, f"{spec.name}: done")


class MemoryRepositoryRegistry(RepositoryRegistry):
    """Repository registry over a name → path dict."""

    def __init__(self, repos: dict[str, Path]) -> None:
        self._repos = repos

    def resolve(self, name: str) -> Path:
        try:
            return self._repos[name]
        except KeyError:
            raise RepositoryNotFound(f"repo {name!r} is not in the registry") from None

    def names(self) -> list[str]:
        return list(self._repos)
