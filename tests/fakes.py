from harness.models import Task
from harness.ports.board import Board, BoardView
from harness.ports.control import TaskControl


class FakeBoardView(BoardView):
    """Board with fixed contents. Lets the API be tested without a projection or queues."""

    def __init__(self, board: Board, tasks: dict[str, Task] | None = None) -> None:
        self._board = board
        self._tasks = tasks or {}

    def snapshot(self) -> Board:
        return self._board

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

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
