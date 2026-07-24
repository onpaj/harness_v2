from harness.models import Task
from harness.ports.board import Board, BoardColumn, BoardTab


def make_task(task_id: str) -> Task:
    return Task(
        id=task_id, workflow_template="default", created="2026-07-19T10:00:00Z"
    )


def test_column_lookup():
    tab = BoardTab(
        name="default",
        columns=(
            BoardColumn(name="plan", tasks=(make_task("tsk_1"),)),
            BoardColumn(name="done", tasks=()),
        ),
    )

    assert tab.column("plan").tasks[0].id == "tsk_1"
    assert tab.column("done").tasks == ()
    assert tab.column("nonexistent") is None


def test_workflow_lookup():
    board = Board(
        revision=3,
        workflows=(
            BoardTab(name="default", columns=(BoardColumn(name="plan", tasks=()),)),
            BoardTab(name="hotfix", columns=(BoardColumn(name="patch", tasks=()),)),
        ),
    )

    assert board.workflow("hotfix").columns[0].name == "patch"
    assert board.workflow("nonexistent") is None


def test_default_tab_prefers_development_then_alphabetical_then_none():
    # workflows is expected pre-sorted (BoardProjection.snapshot()'s contract);
    # default_tab() itself does not re-sort.
    assert Board(revision=0, workflows=(
        BoardTab(name="development", columns=()),
        BoardTab(name="hotfix", columns=()),
    )).default_tab() == "development"
    assert Board(revision=0, workflows=(
        BoardTab(name="alpha", columns=()),
        BoardTab(name="hotfix", columns=()),
    )).default_tab() == "alpha"
    assert Board(revision=0, workflows=()).default_tab() is None


def test_board_serializes_tasks_as_camelcase():
    board = Board(
        revision=7,
        workflows=(
            BoardTab(
                name="default",
                columns=(BoardColumn(name="plan", tasks=(make_task("tsk_1"),)),),
            ),
        ),
    )

    raw = board.to_dict()

    assert raw["revision"] == 7
    assert raw["workflows"][0]["name"] == "default"
    assert raw["workflows"][0]["columns"][0]["name"] == "plan"
    assert raw["workflows"][0]["columns"][0]["tasks"][0]["workflowTemplate"] == "default"
