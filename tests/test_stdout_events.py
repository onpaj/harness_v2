import io

from harness.drivers.stdout_events import StdoutEventSink


def test_emits_one_line_per_event():
    stream = io.StringIO()
    sink = StdoutEventSink(stream=stream)

    sink.emit("dispatched", task_id="tsk_1", to="design")

    line = stream.getvalue().strip()
    assert line.startswith("dispatched")
    assert "task_id=tsk_1" in line
    assert "to=design" in line
    assert "\n" not in line


def test_event_without_fields():
    stream = io.StringIO()
    sink = StdoutEventSink(stream=stream)

    sink.emit("idle")

    assert stream.getvalue().strip() == "idle"


def test_task_snapshot_is_not_printed():
    stream = io.StringIO()
    sink = StdoutEventSink(stream=stream)

    sink.emit("dispatched", task_id="tsk_1", queue="design", task={"id": "tsk_1"})

    line = stream.getvalue().strip()
    assert "task=" not in line
    assert "queue=design" in line
    assert "task_id=tsk_1" in line
