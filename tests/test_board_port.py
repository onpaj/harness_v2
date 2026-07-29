from harness.models import Task
from harness.ports.board import (
    COLUMN_STEP,
    UNKNOWN_WORKFLOW,
    UNKNOWN_WORKFLOW_LABEL,
    AgentActivity,
    Board,
    BoardColumn,
    BoardTab,
)


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


def test_tab_label_renames_only_the_unknown_tab():
    assert BoardTab(name="development", columns=()).label == "development"
    assert BoardTab(name="development", columns=()).is_unknown is False
    # The internal name stays `unknown` — it is the tab's key and its DOM
    # attribute; only what the operator reads changes.
    unknown = BoardTab(name=UNKNOWN_WORKFLOW, columns=())
    assert unknown.is_unknown is True
    assert unknown.label == UNKNOWN_WORKFLOW_LABEL
    assert unknown.name == "unknown"


def test_tab_task_count_sums_every_column():
    tab = BoardTab(
        name="default",
        columns=(
            BoardColumn(name="plan", tasks=(make_task("tsk_1"), make_task("tsk_2"))),
            BoardColumn(name="done", tasks=(make_task("tsk_3"),)),
            BoardColumn(name="failed", tasks=()),
        ),
    )

    assert tab.task_count == 3


def test_column_to_dict_carries_kind_and_description():
    raw = BoardColumn(
        name="plan", tasks=(), kind=COLUMN_STEP, description="break it down"
    ).to_dict()

    assert raw["kind"] == COLUMN_STEP
    assert raw["description"] == "break it down"


def test_agent_activity_to_dict_carries_token_fields():
    activity = AgentActivity(
        task_id="tsk_1",
        title="Fix the bug",
        at="2026-07-19T10:00:00Z",
        outcome="done",
        summary="planned it",
        reason=None,
        input_tokens=120,
        output_tokens=45,
        model="claude-sonnet-5",
    )

    raw = activity.to_dict()

    assert raw["inputTokens"] == 120
    assert raw["outputTokens"] == 45
    assert raw["model"] == "claude-sonnet-5"


def test_agent_activity_token_fields_default_none():
    activity = AgentActivity(
        task_id="tsk_1",
        title="Fix the bug",
        at="2026-07-19T10:00:00Z",
        outcome="done",
        summary="planned it",
        reason=None,
    )

    raw = activity.to_dict()

    assert raw["inputTokens"] is None
    assert raw["outputTokens"] is None
    assert raw["model"] is None


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
