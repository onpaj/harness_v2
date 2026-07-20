import asyncio

import pytest

from harness.drivers.stage_output import StageOutputProjection
from harness.ports.logs import StageOutputView


def test_projection_is_a_stage_output_view():
    assert isinstance(StageOutputProjection(), StageOutputView)


def test_stage_output_events_append_to_tail():
    proj = StageOutputProjection()

    proj.emit("stage_output", task_id="t1", line="hello")
    proj.emit("stage_output", task_id="t1", line="world")

    assert proj.tail("t1") == ("hello", "world")


def test_tail_is_empty_for_unknown_task():
    assert StageOutputProjection().tail("nope") == ()


def test_claimed_resets_the_buffer():
    proj = StageOutputProjection()
    proj.emit("stage_output", task_id="t1", line="old attempt")

    proj.emit("claimed", task_id="t1")

    assert proj.tail("t1") == ()


def test_events_without_task_id_are_ignored():
    proj = StageOutputProjection()

    proj.emit("stage_output", line="no id")  # no task_id
    proj.emit("consumed", task={"id": "x"}, queue="done")  # board event, no task_id

    assert proj.tail("t1") == ()


def test_stage_output_without_line_is_ignored():
    proj = StageOutputProjection()

    proj.emit("stage_output", task_id="t1")  # no line

    assert proj.tail("t1") == ()


def test_buffer_is_bounded_by_max_lines():
    proj = StageOutputProjection(max_lines=3)

    for index in range(5):
        proj.emit("stage_output", task_id="t1", line=str(index))

    assert proj.tail("t1") == ("2", "3", "4")


def test_buffers_are_isolated_per_task():
    proj = StageOutputProjection()

    proj.emit("stage_output", task_id="t1", line="a")
    proj.emit("stage_output", task_id="t2", line="b")

    assert proj.tail("t1") == ("a",)
    assert proj.tail("t2") == ("b",)


async def test_subscribe_yields_buffered_then_live_lines():
    proj = StageOutputProjection()
    proj.emit("claimed", task_id="t1")  # stage running
    proj.emit("stage_output", task_id="t1", line="a")  # buffered before subscribe

    stream = proj.subscribe("t1")
    assert await anext(stream) == "a"  # replays the buffer; registers the queue

    proj.emit("stage_output", task_id="t1", line="b")  # live
    assert await asyncio.wait_for(anext(stream), 1.0) == "b"

    await stream.aclose()


async def test_subscribe_completes_on_consumed():
    proj = StageOutputProjection()
    proj.emit("claimed", task_id="t1")
    proj.emit("stage_output", task_id="t1", line="a")

    stream = proj.subscribe("t1")
    assert await anext(stream) == "a"

    proj.emit("consumed", task_id="t1")
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), 1.0)


async def test_subscribe_completes_on_failed():
    proj = StageOutputProjection()
    proj.emit("claimed", task_id="t1")
    proj.emit("stage_output", task_id="t1", line="a")

    stream = proj.subscribe("t1")
    assert await anext(stream) == "a"

    proj.emit("failed", task_id="t1")
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), 1.0)


async def test_finished_stage_drops_its_buffer():
    """Live-only scope: when the stage ends its buffer is evicted, so a client
    connecting afterwards gets nothing and the stream ends at once (and the
    projection doesn't accumulate a buffer per task forever)."""
    proj = StageOutputProjection()
    proj.emit("claimed", task_id="t1")
    proj.emit("stage_output", task_id="t1", line="a")
    proj.emit("consumed", task_id="t1")  # stage finished → buffer dropped

    assert proj.tail("t1") == ()

    stream = proj.subscribe("t1")
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), 1.0)


async def test_subscribe_to_idle_task_ends_immediately():
    proj = StageOutputProjection()

    stream = proj.subscribe("never-started")
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), 1.0)


async def test_multiple_subscribers_each_receive_live_lines():
    proj = StageOutputProjection()
    proj.emit("claimed", task_id="t1")
    proj.emit("stage_output", task_id="t1", line="seed")

    first = proj.subscribe("t1")
    second = proj.subscribe("t1")
    assert await anext(first) == "seed"
    assert await anext(second) == "seed"

    proj.emit("stage_output", task_id="t1", line="live")
    assert await asyncio.wait_for(anext(first), 1.0) == "live"
    assert await asyncio.wait_for(anext(second), 1.0) == "live"

    await first.aclose()
    await second.aclose()


async def test_subscriber_is_removed_after_close():
    proj = StageOutputProjection()
    proj.emit("claimed", task_id="t1")
    stream = proj.subscribe("t1")
    proj.emit("stage_output", task_id="t1", line="x")
    assert await anext(stream) == "x"

    await stream.aclose()

    # No subscribers remain, so a subsequent emit is a plain buffer append.
    proj.emit("stage_output", task_id="t1", line="y")
    assert proj.tail("t1") == ("x", "y")
