import asyncio

from harness.drivers.memory import MemoryTaskQueue
from harness.models import END, Task, Transition, Workflow
from harness.ports.board import DONE_COLUMN, FAILED_COLUMN
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


def make_task(task_id="tsk_1", status=None, created="2026-07-19T10:00:00Z", **kwargs):
    return Task(
        id=task_id,
        workflow_template="default",
        created=created,
        status=status,
        **kwargs,
    )


def test_column_order_follows_reachability_and_ignores_back_edges():
    assert column_order(WORKFLOW) == (
        "plan",
        "design",
        "development",
        "review",
        DONE_COLUMN,
        FAILED_COLUMN,
    )


def test_snapshot_has_every_column_even_when_empty():
    projection = BoardProjection(WORKFLOW)

    board = projection.snapshot()

    assert [column.name for column in board.columns] == list(column_order(WORKFLOW))
    assert all(column.tasks == () for column in board.columns)


def test_apply_places_task_in_column():
    projection = BoardProjection(WORKFLOW)

    projection.apply("design", make_task(status="design"))

    assert projection.snapshot().column("design").tasks[0].id == "tsk_1"


def test_apply_moves_task_between_columns():
    projection = BoardProjection(WORKFLOW)
    projection.apply("design", make_task(status="design"))

    projection.apply("development", make_task(status="development"))

    assert projection.snapshot().column("design").tasks == ()
    assert projection.snapshot().column("development").tasks[0].id == "tsk_1"


def test_apply_handles_backward_edge():
    projection = BoardProjection(WORKFLOW)
    projection.apply("review", make_task(status="review"))

    projection.apply("development", make_task(status="development"))

    assert projection.snapshot().column("development").tasks[0].status == "development"


def test_apply_to_terminal_columns():
    projection = BoardProjection(WORKFLOW)

    projection.apply(DONE_COLUMN, make_task(status="end"))
    projection.apply(FAILED_COLUMN, make_task(task_id="tsk_2"))

    assert projection.snapshot().column(DONE_COLUMN).tasks[0].id == "tsk_1"
    assert projection.snapshot().column(FAILED_COLUMN).tasks[0].id == "tsk_2"


def test_unknown_column_is_ignored():
    projection = BoardProjection(WORKFLOW)

    projection.apply("nonsense", make_task(status="nonsense"))

    assert projection.get("tsk_1") is None


def test_tasks_are_ordered_by_created():
    projection = BoardProjection(WORKFLOW)
    projection.apply("plan", make_task("tsk_2", "plan", created="2026-07-19T10:00:05Z"))
    projection.apply("plan", make_task("tsk_1", "plan", created="2026-07-19T10:00:00Z"))

    ids = [task.id for task in projection.snapshot().column("plan").tasks]

    assert ids == ["tsk_1", "tsk_2"]


def test_get_returns_full_task():
    projection = BoardProjection(WORKFLOW)
    projection.apply("plan", make_task(status="plan", last_outcome="done"))

    assert projection.get("tsk_1").last_outcome == "done"
    assert projection.get("unknown") is None


def test_revision_grows_monotonically():
    projection = BoardProjection(WORKFLOW)
    first = projection.snapshot().revision

    projection.apply("plan", make_task(status="plan"))
    second = projection.snapshot().revision
    projection.apply("design", make_task(status="design"))
    third = projection.snapshot().revision

    assert first < second < third


def test_hydrate_reads_every_source():
    projection = BoardProjection(WORKFLOW)
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

    board = projection.snapshot()
    assert board.column("plan").tasks[0].id == "tsk_1"
    assert board.column("review").tasks[0].id == "tsk_2"
    assert board.column(DONE_COLUMN).tasks[0].id == "tsk_3"
    assert board.column(FAILED_COLUMN).tasks[0].id == "tsk_4"
    assert board.column("design").tasks[0].id == "tsk_5"


def test_hydrate_skips_inbox_task_without_status():
    projection = BoardProjection(WORKFLOW)
    inbox = MemoryTaskQueue("tasks")
    inbox.put(make_task("tsk_1"))

    projection.hydrate(
        inbox=inbox,
        step_queues={},
        done=MemoryTaskQueue("done"),
        failed=MemoryTaskQueue("failed"),
    )

    assert projection.get("tsk_1") is None


async def test_subscribe_yields_current_revision_first():
    projection = BoardProjection(WORKFLOW)
    projection.apply("plan", make_task(status="plan"))

    stream = projection.subscribe()
    first = await anext(stream)

    assert first == projection.snapshot().revision
    await stream.aclose()


async def test_subscribe_wakes_on_change():
    projection = BoardProjection(WORKFLOW)
    stream = projection.subscribe()
    await anext(stream)

    projection.apply("plan", make_task(status="plan"))
    revision = await asyncio.wait_for(anext(stream), timeout=1.0)

    assert revision == projection.snapshot().revision
    await stream.aclose()


async def test_subscriber_that_falls_behind_does_not_block_applying():
    projection = BoardProjection(WORKFLOW)
    stream = projection.subscribe()
    await anext(stream)

    for index in range(50):
        projection.apply("plan", make_task(f"tsk_{index}", "plan"))

    assert len(projection.snapshot().column("plan").tasks) == 50
    assert await asyncio.wait_for(anext(stream), timeout=1.0) == projection.snapshot().revision
    await stream.aclose()
