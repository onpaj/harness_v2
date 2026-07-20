from harness.models import Task
from harness.ports.board import Board, BoardColumn


def make_task(task_id: str) -> Task:
    return Task(
        id=task_id, workflow_template="default", created="2026-07-19T10:00:00Z"
    )


def test_column_lookup():
    board = Board(
        revision=3,
        columns=(
            BoardColumn(name="plan", tasks=(make_task("tsk_1"),)),
            BoardColumn(name="done", tasks=()),
        ),
    )

    assert board.column("plan").tasks[0].id == "tsk_1"
    assert board.column("done").tasks == ()
    assert board.column("nonexistent") is None


def test_board_serializes_tasks_as_camelcase():
    board = Board(
        revision=7,
        columns=(BoardColumn(name="plan", tasks=(make_task("tsk_1"),)),),
    )

    raw = board.to_dict()

    assert raw["revision"] == 7
    assert raw["columns"][0]["name"] == "plan"
    assert raw["columns"][0]["tasks"][0]["workflowTemplate"] == "default"
