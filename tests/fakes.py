from harness.models import Task
from harness.ports.board import AgentActivity, Board, BoardView
from harness.ports.control import TaskControl
from harness.ports.issue_import import IssueImport, IssueImportResult


class FakeBoardView(BoardView):
    """Board with fixed contents. Lets the API be tested without a projection or queues."""

    def __init__(self, board: Board, tasks: dict[str, Task] | None = None) -> None:
        self._board = board
        self._tasks = tasks or {}

    def snapshot(self) -> Board:
        return self._board

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def agent_history(self, name: str) -> tuple[AgentActivity, ...]:
        # Same derivation as BoardProjection.agent_history — a `consumer:<name>`
        # entry per handling, newest first — so the API tests exercise the real
        # shape without wiring a projection.
        actor = f"consumer:{name}"
        activities = [
            AgentActivity(
                task_id=task.id,
                title=task.data.get("title") or task.id,
                at=entry.at,
                outcome=entry.outcome,
                summary=entry.summary,
                reason=entry.reason,
            )
            for task in self._tasks.values()
            for entry in task.history
            if entry.actor == actor
        ]
        activities.sort(key=lambda activity: (activity.at, activity.task_id), reverse=True)
        return tuple(activities)

    async def subscribe(self):
        yield self._board.revision


class FakeTaskControl(TaskControl):
    """Records restart/delete calls; returns configurable results. Lets the
    API be tested without queues."""

    def __init__(self, result: bool = True, delete_result: bool = True) -> None:
        self._result = result
        self._delete_result = delete_result
        self.restarted: list[str] = []
        self.deleted: list[str] = []

    def restart(self, task_id: str) -> bool:
        self.restarted.append(task_id)
        return self._result

    def delete(self, task_id: str) -> bool:
        self.deleted.append(task_id)
        return self._delete_result


class FakeIssueImport(IssueImport):
    """Records `add()` calls; returns a scripted result per ref (falling back
    to a generic success). Lets the API be tested without GitHub or queues."""

    def __init__(self, results: dict[str, IssueImportResult] | None = None) -> None:
        self._results = results or {}
        self.calls: list[str] = []

    def add(self, ref: str) -> IssueImportResult:
        self.calls.append(ref)
        if ref in self._results:
            return self._results[ref]
        return IssueImportResult(ref=ref, ok=True, task_id=f"tsk_{ref}")
