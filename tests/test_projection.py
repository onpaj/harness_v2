import asyncio

from harness.drivers.memory import MemoryTaskQueue
from harness.models import END, Task, Transition, Workflow
from harness.ports.board import DONE_COLUMN, FAILED_COLUMN, TODO_COLUMN, UNKNOWN_WORKFLOW
from harness.projection import BoardProjection, column_order

WORKFLOW = Workflow(
    name="default",
    start="plan",
    transitions=(
        Transition(from_step="plan", on="done", to_step="design"),
        Transition(from_step="design", on="done", to_step="development"),
        Transition(from_step="development", on="done", to_step="review"),
        Transition(from_step="review", on="done", to_step=END),
        Transition(from_step="review", on="request_changes", to_step="development"),
    ),
)

HOTFIX = Workflow(
    name="hotfix",
    start="patch",
    transitions=(Transition(from_step="patch", on="done", to_step=END),),
)


def make_task(
    task_id="tsk_1",
    status=None,
    created="2026-07-19T10:00:00Z",
    workflow_template="default",
    **kwargs,
):
    return Task(
        id=task_id,
        workflow_template=workflow_template,
        created=created,
        status=status,
        **kwargs,
    )


def test_column_order_follows_reachability_and_ignores_back_edges():
    assert column_order(WORKFLOW.steps(), (WORKFLOW,)) == (
        TODO_COLUMN,
        "plan",
        "design",
        "development",
        "review",
        DONE_COLUMN,
        FAILED_COLUMN,
    )


def test_column_order_unions_multiple_workflows_no_duplicates():
    """A second workflow contributes only the steps not already seen, in its
    own order, and a step shared by both (here "plan") shows up once."""
    other = Workflow(
        name="hotfix",
        start="plan",
        transitions=(
            Transition(from_step="plan", on="done", to_step="review"),
            Transition(from_step="review", on="done", to_step=END),
        ),
    )

    assert column_order((*WORKFLOW.steps(), *other.steps()), (WORKFLOW, other)) == (
        TODO_COLUMN,
        "plan",
        "design",
        "development",
        "review",
        DONE_COLUMN,
        FAILED_COLUMN,
    )


RESOLVER_WORKFLOW = Workflow(
    name="resolver",
    start="resolve",
    transitions=(
        Transition(from_step="resolve", on="done", to_step="land"),
        Transition(from_step="land", on="done", to_step=END),
    ),
)


def test_column_order_falls_back_to_declaration_order_for_workflow_less_steps():
    assert column_order((*WORKFLOW.steps(), "triage"), (WORKFLOW,)) == (
        TODO_COLUMN,
        "plan",
        "design",
        "development",
        "review",
        "triage",
        DONE_COLUMN,
        FAILED_COLUMN,
    )


def test_column_order_folds_in_extra_workflow_steps():
    assert column_order((), (WORKFLOW, RESOLVER_WORKFLOW)) == (
        TODO_COLUMN,
        "plan",
        "design",
        "development",
        "review",
        "resolve",
        "land",
        DONE_COLUMN,
        FAILED_COLUMN,
    )


def test_column_order_with_no_workflow_uses_declaration_order():
    assert column_order(("triage",)) == (
        TODO_COLUMN,
        "triage",
        DONE_COLUMN,
        FAILED_COLUMN,
    )


def test_snapshot_with_extra_workflow_includes_its_columns():
    projection = BoardProjection((), (WORKFLOW, RESOLVER_WORKFLOW))

    board = projection.snapshot()

    # Each served workflow is its own tab; the resolver tab carries its columns.
    tab = board.workflow("resolver")
    assert [column.name for column in tab.columns] == list(
        column_order((), (RESOLVER_WORKFLOW,))
    )


def test_apply_places_resolver_task_in_resolve_column():
    projection = BoardProjection((), (WORKFLOW, RESOLVER_WORKFLOW))

    projection.apply(
        "resolve", make_task(status="resolve", workflow_template="resolver")
    )

    assert (
        projection.snapshot().workflow("resolver").column("resolve").tasks[0].id
        == "tsk_1"
    )


def test_snapshot_has_every_column_even_when_empty():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))

    board = projection.snapshot()

    tab = board.workflow("default")
    assert [column.name for column in tab.columns] == list(
        column_order((), (WORKFLOW,))
    )
    assert all(column.tasks == () for column in tab.columns)


def test_apply_places_task_in_column():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))

    projection.apply("design", make_task(status="design"))

    assert projection.snapshot().workflow("default").column("design").tasks[0].id == "tsk_1"


def test_apply_moves_task_between_columns():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    projection.apply("design", make_task(status="design"))

    projection.apply("development", make_task(status="development"))

    tab = projection.snapshot().workflow("default")
    assert tab.column("design").tasks == ()
    assert tab.column("development").tasks[0].id == "tsk_1"


def test_apply_handles_backward_edge():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    projection.apply("review", make_task(status="review"))

    projection.apply("development", make_task(status="development"))

    tab = projection.snapshot().workflow("default")
    assert tab.column("development").tasks[0].status == "development"


def test_apply_to_terminal_columns():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))

    projection.apply(DONE_COLUMN, make_task(status="end"))
    projection.apply(FAILED_COLUMN, make_task(task_id="tsk_2"))

    tab = projection.snapshot().workflow("default")
    assert tab.column(DONE_COLUMN).tasks[0].id == "tsk_1"
    assert tab.column(FAILED_COLUMN).tasks[0].id == "tsk_2"


def test_unknown_column_is_ignored():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))

    projection.apply("nonsense", make_task(status="nonsense"))

    assert projection.get("tsk_1") is None


def test_tasks_are_ordered_by_created():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    projection.apply("plan", make_task("tsk_2", "plan", created="2026-07-19T10:00:05Z"))
    projection.apply("plan", make_task("tsk_1", "plan", created="2026-07-19T10:00:00Z"))

    ids = [task.id for task in projection.snapshot().workflow("default").column("plan").tasks]

    assert ids == ["tsk_1", "tsk_2"]


def test_get_returns_full_task():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    projection.apply("plan", make_task(status="plan", last_outcome="done"))

    assert projection.get("tsk_1").last_outcome == "done"
    assert projection.get("unknown") is None


def test_revision_grows_monotonically():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    first = projection.snapshot().revision

    projection.apply("plan", make_task(status="plan"))
    second = projection.snapshot().revision
    projection.apply("design", make_task(status="design"))
    third = projection.snapshot().revision

    assert first < second < third


def test_hydrate_reads_every_source():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    inbox = MemoryTaskQueue("tasks")
    plan = MemoryTaskQueue("plan")
    review = MemoryTaskQueue("review")
    done = MemoryTaskQueue("done")
    failed = MemoryTaskQueue("failed")
    plan.put(make_task("tsk_1", "plan"))
    review.put(make_task("tsk_2", "review"))
    done.put(make_task("tsk_3", "end"))
    failed.put(make_task("tsk_4", "design"))
    inbox.put(make_task("tsk_5", "design"))

    projection.hydrate(
        inbox=inbox,
        step_queues={"plan": plan, "review": review},
        done=done,
        failed=failed,
    )

    tab = projection.snapshot().workflow("default")
    assert tab.column("plan").tasks[0].id == "tsk_1"
    assert tab.column("review").tasks[0].id == "tsk_2"
    assert tab.column(DONE_COLUMN).tasks[0].id == "tsk_3"
    assert tab.column(FAILED_COLUMN).tasks[0].id == "tsk_4"
    assert tab.column("design").tasks[0].id == "tsk_5"


def test_hydrate_places_statusless_inbox_task_in_todo():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    inbox = MemoryTaskQueue("tasks")
    inbox.put(make_task("tsk_1"))

    projection.hydrate(
        inbox=inbox,
        step_queues={},
        done=MemoryTaskQueue("done"),
        failed=MemoryTaskQueue("failed"),
    )

    assert projection.snapshot().workflow("default").column(TODO_COLUMN).tasks[0].id == "tsk_1"


def test_apply_moves_task_from_failed_to_todo():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    projection.apply(FAILED_COLUMN, make_task(status="failed"))

    projection.apply(TODO_COLUMN, make_task(status=None))

    tab = projection.snapshot().workflow("default")
    assert tab.column(FAILED_COLUMN).tasks == ()
    assert tab.column(TODO_COLUMN).tasks[0].id == "tsk_1"


def test_archive_removes_task_from_its_column_but_keeps_it_fetchable():
    projection = BoardProjection([WORKFLOW])
    projection.apply(DONE_COLUMN, make_task(status="end"))

    projection.archive(make_task(status="end", last_outcome="done"))

    assert projection.snapshot().workflow("default").column(DONE_COLUMN).tasks == ()
    assert projection.get("tsk_1") is not None
    assert projection.get("tsk_1").last_outcome == "done"


def test_archive_bumps_the_revision():
    projection = BoardProjection([WORKFLOW])
    projection.apply(DONE_COLUMN, make_task(status="end"))
    before = projection.snapshot().revision

    projection.archive(make_task(status="end"))

    assert projection.snapshot().revision > before


def test_hydrate_with_archived_queue_keeps_tasks_fetchable_but_off_the_board():
    projection = BoardProjection([WORKFLOW])
    archived = MemoryTaskQueue("archived")
    archived.put(make_task("tsk_9", "end"))

    projection.hydrate(
        inbox=MemoryTaskQueue("tasks"),
        step_queues={},
        done=MemoryTaskQueue("done"),
        failed=MemoryTaskQueue("failed"),
        archived=archived,
    )

    assert projection.get("tsk_9") is not None
    for tab in projection.snapshot().workflows:
        for column in tab.columns:
            assert all(task.id != "tsk_9" for task in column.tasks)


def test_hydrate_without_archived_queue_is_backward_compatible():
    projection = BoardProjection([WORKFLOW])

    projection.hydrate(
        inbox=MemoryTaskQueue("tasks"),
        step_queues={},
        done=MemoryTaskQueue("done"),
        failed=MemoryTaskQueue("failed"),
    )

    assert projection.snapshot().revision == 1


async def test_subscribe_yields_current_revision_first():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    projection.apply("plan", make_task(status="plan"))

    stream = projection.subscribe()
    first = await anext(stream)

    assert first == projection.snapshot().revision
    await stream.aclose()


async def test_subscribe_wakes_on_change():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    stream = projection.subscribe()
    await anext(stream)

    projection.apply("plan", make_task(status="plan"))
    revision = await asyncio.wait_for(anext(stream), timeout=1.0)

    assert revision == projection.snapshot().revision
    await stream.aclose()


async def test_subscriber_that_falls_behind_does_not_block_applying():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    stream = projection.subscribe()
    await anext(stream)

    for index in range(50):
        projection.apply("plan", make_task(f"tsk_{index}", "plan"))

    assert len(projection.snapshot().workflow("default").column("plan").tasks) == 50
    assert await asyncio.wait_for(anext(stream), timeout=1.0) == projection.snapshot().revision
    await stream.aclose()


# --- Multi-workflow tabs (FR-3, FR-4, FR-6) ---------------------------------


def test_tasks_land_in_the_tab_matching_their_own_template():
    projection = BoardProjection((), [WORKFLOW, HOTFIX])

    projection.apply("plan", make_task("tsk_1", "plan", workflow_template="default"))
    projection.apply("patch", make_task("tsk_2", "patch", workflow_template="hotfix"))

    board = projection.snapshot()
    default_tab = board.workflow("default")
    hotfix_tab = board.workflow("hotfix")
    assert default_tab.column("plan").tasks[0].id == "tsk_1"
    assert hotfix_tab.column("patch").tasks[0].id == "tsk_2"


def test_same_step_name_in_two_workflows_stays_isolated():
    other = Workflow(
        name="hotfix",
        start="plan",
        transitions=(Transition(from_step="plan", on="done", to_step=END),),
    )
    projection = BoardProjection((), [WORKFLOW, other])

    projection.apply("plan", make_task("tsk_1", "plan", workflow_template="default"))
    projection.apply("plan", make_task("tsk_2", "plan", workflow_template="hotfix"))

    board = projection.snapshot()
    assert [t.id for t in board.workflow("default").column("plan").tasks] == ["tsk_1"]
    assert [t.id for t in board.workflow("hotfix").column("plan").tasks] == ["tsk_2"]


def test_unrecognized_template_falls_back_to_unknown_tab():
    projection = BoardProjection((), [WORKFLOW])

    projection.apply(FAILED_COLUMN, make_task("tsk_1", "failed", workflow_template="ghost"))

    board = projection.snapshot()
    assert board.workflow(UNKNOWN_WORKFLOW).column(FAILED_COLUMN).tasks[0].id == "tsk_1"
    assert board.workflow("default").column(FAILED_COLUMN).tasks == ()


def test_unknown_tab_is_omitted_when_empty():
    projection = BoardProjection((), [WORKFLOW])

    board = projection.snapshot()

    assert board.workflow(UNKNOWN_WORKFLOW) is None
    assert [tab.name for tab in board.workflows] == ["default"]


def test_hydrate_puts_unrecognized_template_inbox_task_in_unknown_todo():
    """A task with a typo'd workflow_template sitting in the inbox before the
    dispatcher's first tick must still be visible — not silently dropped —
    which requires TODO_COLUMN in the unknown tab's column set (FR-4)."""
    projection = BoardProjection((), [WORKFLOW])
    inbox = MemoryTaskQueue("tasks")
    inbox.put(make_task("tsk_1", workflow_template="ghost"))

    projection.hydrate(
        inbox=inbox,
        step_queues={},
        done=MemoryTaskQueue("done"),
        failed=MemoryTaskQueue("failed"),
    )

    board = projection.snapshot()
    assert board.workflow(UNKNOWN_WORKFLOW).column(TODO_COLUMN).tasks[0].id == "tsk_1"


def test_snapshot_tabs_are_sorted_alphabetically():
    projection = BoardProjection((), [HOTFIX, WORKFLOW])

    board = projection.snapshot()

    assert [tab.name for tab in board.workflows] == ["default", "hotfix"]
