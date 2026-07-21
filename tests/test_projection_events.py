from harness.drivers.projection_events import ProjectionSink
from harness.models import END, Task, Transition, Workflow
from harness.ports.board import DONE_COLUMN
from harness.projection import BoardProjection

WORKFLOW = Workflow(
    name="default",
    start="plan",
    transitions=(
        Transition(from_step="plan", on="done", to_step="review"),
        Transition(from_step="review", on="done", to_step=END),
    ),
)


def snapshot(status="plan", task_id="tsk_1", **kwargs) -> dict:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status=status,
        **kwargs,
    ).to_dict()


def build():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    return projection, ProjectionSink(projection)


def test_event_with_task_and_queue_lands_on_board():
    projection, sink = build()

    sink.emit("dispatched", task_id="tsk_1", queue="plan", task=snapshot())

    assert projection.snapshot().column("plan").tasks[0].id == "tsk_1"


def test_terminal_event_lands_in_done():
    projection, sink = build()

    sink.emit("finished", task_id="tsk_1", queue="done", task=snapshot("end"))

    assert projection.snapshot().column(DONE_COLUMN).tasks[0].id == "tsk_1"


def test_event_without_task_is_ignored():
    projection, sink = build()

    sink.emit("started", workflows=["default"])
    sink.emit("recovered", count=3)
    sink.emit("corrupt", path="/tmp/x.json")

    assert projection.snapshot().revision == 0


def test_event_with_task_but_no_queue_is_ignored():
    projection, sink = build()

    sink.emit("weird", task=snapshot())

    assert projection.get("tsk_1") is None


def test_malformed_task_snapshot_does_not_raise():
    projection, sink = build()

    sink.emit("dispatched", queue="plan", task={"something": "else"})

    assert projection.snapshot().revision == 0
