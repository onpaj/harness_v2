from harness.drivers.composite_events import CompositeEventSink
from harness.drivers.memory import MemoryEventSink
from harness.ports.events import EventSink


class ExplodingSink(EventSink):
    def emit(self, name, **fields):
        raise RuntimeError("sink blew up")


def test_event_reaches_every_sink():
    first = MemoryEventSink()
    second = MemoryEventSink()
    composite = CompositeEventSink(first, second)

    composite.emit("dispatched", task_id="tsk_1")

    assert first.events == [("dispatched", {"task_id": "tsk_1"})]
    assert second.events == [("dispatched", {"task_id": "tsk_1"})]


def test_broken_sink_does_not_stop_the_others():
    survivor = MemoryEventSink()
    composite = CompositeEventSink(ExplodingSink(), survivor)

    composite.emit("dispatched", task_id="tsk_1")

    assert survivor.names() == ["dispatched"]


def test_no_sinks_is_not_an_error():
    CompositeEventSink().emit("dispatched", task_id="tsk_1")


def test_broken_sink_is_reported_on_stderr(capsys):
    composite = CompositeEventSink(ExplodingSink(), MemoryEventSink())

    composite.emit("dispatched", task_id="tsk_1")

    assert "sink blew up" in capsys.readouterr().err
