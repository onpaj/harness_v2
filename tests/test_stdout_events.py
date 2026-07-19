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
