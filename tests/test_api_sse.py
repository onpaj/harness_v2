import asyncio

from fastapi.testclient import TestClient

from harness.api.app import create_app
from harness.api.routes import board_event_stream
from harness.drivers.memory import FakeClock
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
    projection = BoardProjection(WORKFLOW)
    projection.apply("plan", make_task())
    stream = board_event_stream(projection, FakeClock(), 0.25)

    frame = await anext(stream)

    assert frame == 'event: board\ndata: {"revision": 1}\n\n'
    await stream.aclose()


async def test_stream_emits_frame_per_change():
    projection = BoardProjection(WORKFLOW)
    stream = board_event_stream(projection, FakeClock(), 0.25)
    await anext(stream)

    projection.apply("plan", make_task())
    frame = await asyncio.wait_for(anext(stream), timeout=1.0)

    assert '"revision": 1' in frame
    await stream.aclose()


async def test_stream_waits_between_frames_to_coalesce():
    clock = FakeClock()
    projection = BoardProjection(WORKFLOW)
    stream = board_event_stream(projection, clock, 0.25)
    await anext(stream)

    projection.apply("plan", make_task())
    await asyncio.wait_for(anext(stream), timeout=1.0)

    assert clock.slept == [0.25]
    await stream.aclose()


def test_events_endpoint_is_registered():
    projection = BoardProjection(WORKFLOW)
    client = TestClient(create_app(view=projection, clock=FakeClock()))

    paths: set[str] = set()
    for route in client.app.routes:
        if hasattr(route, "path"):
            paths.add(route.path)
        elif hasattr(route, "original_router"):
            paths.update(r.path for r in route.original_router.routes)

    assert "/api/events" in paths
