from harness.models import Task
from harness.ports.board import Board, BoardView


class FakeBoardView(BoardView):
    """Board s pevným obsahem. API se tak testuje bez projekce a bez front."""

    def __init__(self, board: Board, tasks: dict[str, Task] | None = None) -> None:
        self._board = board
        self._tasks = tasks or {}

    def snapshot(self) -> Board:
        return self._board

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    async def subscribe(self):
        yield self._board.revision
