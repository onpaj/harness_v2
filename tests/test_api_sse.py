import asyncio

from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.api.routes import board_event_stream, stage_output_stream
from harness.drivers.memory import FakeClock
from harness.drivers.stage_output import StageOutputProjection
from harness.models import END, Task, Transition, Workflow
from harness.projection import BoardProjection

WORKFLOW = Workflow(
    name="default",
    start="plan",
    transitions=(
        Transition(from_step="plan", on="done", to_step="review"),
        Transition(from_step="review", on="done", to_step=END),
    ),
)


def make_task(task_id="tsk_1") -> Task:
    return Task(
        id=task_id,
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="plan",
    )


async def test_stream_emits_current_revision_first():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    projection.apply("plan", make_task())
    stream = board_event_stream(projection, FakeClock(), 0.25)

    frame = await anext(stream)

    assert frame == 'event: board\ndata: {"revision": 1}\n\n'
    await stream.aclose()


async def test_stream_emits_frame_per_change():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    stream = board_event_stream(projection, FakeClock(), 0.25)
    await anext(stream)

    projection.apply("plan", make_task())
    frame = await asyncio.wait_for(anext(stream), timeout=1.0)

    assert '"revision": 1' in frame
    await stream.aclose()


async def test_stream_waits_between_frames_to_coalesce():
    clock = FakeClock()
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    stream = board_event_stream(projection, clock, 0.25)
    await anext(stream)

    projection.apply("plan", make_task())
    await asyncio.wait_for(anext(stream), timeout=1.0)

    assert clock.slept == [0.25]
    await stream.aclose()


def test_events_endpoint_is_registered():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    client = TestClient(create_app(view=projection, clock=FakeClock()))

    assert client.app.url_path_for("events") == "/api/events"


# --- stage output stream -----------------------------------------------------


async def test_stage_output_stream_wraps_and_escapes_lines():
    output = StageOutputProjection()
    output.emit("claimed", task_id="t1")

    frames: list[str] = []

    async def drain() -> None:
        async for frame in stage_output_stream(output, "t1"):
            frames.append(frame)

    task = asyncio.ensure_future(drain())
    await asyncio.sleep(0)  # let the stream subscribe while the stage runs

    output.emit("stage_output", task_id="t1", line="⏵ Bash: pytest -q")
    output.emit("stage_output", task_id="t1", line="<script>alert(1)</script>")
    output.emit("consumed", task_id="t1")  # ends the stream
    await asyncio.wait_for(task, 1.0)

    assert frames[0] == "event: line\ndata: <div>⏵ Bash: pytest -q</div>\n\n"
    assert (
        frames[1]
        == "event: line\ndata: <div>&lt;script&gt;alert(1)&lt;/script&gt;</div>\n\n"
    )
    assert frames[-1] == "event: end\ndata: \n\n"


async def test_stage_output_stream_collapses_stray_newline_in_a_line():
    """A newline inside a line would truncate the SSE frame — the endpoint must
    collapse it so exactly one `data:` field is emitted per line."""
    output = StageOutputProjection()
    output.emit("claimed", task_id="t1")

    frames: list[str] = []

    async def drain() -> None:
        async for frame in stage_output_stream(output, "t1"):
            frames.append(frame)

    task = asyncio.ensure_future(drain())
    await asyncio.sleep(0)
    output.emit("stage_output", task_id="t1", line="para one\n\npara two")
    output.emit("consumed", task_id="t1")
    await asyncio.wait_for(task, 1.0)

    assert frames[0] == "event: line\ndata: <div>para one  para two</div>\n\n"
    # Exactly one data field → no bare newline mid-frame.
    assert frames[0].count("data:") == 1


def test_task_output_endpoint_is_registered():
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    client = TestClient(create_app(view=projection, clock=FakeClock()))

    assert (
        client.app.url_path_for("task_output", task_id="t1")
        == "/api/tasks/t1/output/events"
    )


def test_task_output_endpoint_closes_for_finished_stage():
    """A GET after the stage ended must terminate (not hang) with an end event —
    the buffer is gone (live-only), so no lines are replayed."""
    projection = BoardProjection(WORKFLOW.steps(), (WORKFLOW,))
    output = StageOutputProjection()
    output.emit("claimed", task_id="t1")
    output.emit("stage_output", task_id="t1", line="hello")
    output.emit("consumed", task_id="t1")
    client = TestClient(
        create_app(view=projection, output=output, clock=FakeClock())
    )

    response = client.get("/api/tasks/t1/output/events")

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert response.text == "event: end\ndata: \n\n"
