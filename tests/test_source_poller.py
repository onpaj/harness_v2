"""SourcePoller — jádro plnící inbox ze zdroje."""

from harness.drivers.memory import FakeClock, MemoryEventSink, MemoryTaskQueue, MemoryTaskSource
from harness.ports.source import TaskSource
from harness.source_poller import SourcePoller


class RaisingSource(TaskSource):
    kind = "boom"

    def poll(self):
        raise RuntimeError("GitHub je dole")

    def report_progress(self, task, progress):  # pragma: no cover - nevolá se
        pass

    def finish(self, task, result):  # pragma: no cover - nevolá se
        pass


def test_tick_moves_submitted_task_into_inbox_and_emits_ingested():
    source = MemoryTaskSource(clock=FakeClock())
    source.submit("Fix bug")
    inbox = MemoryTaskQueue("tasks")
    events = MemoryEventSink()
    poller = SourcePoller(source=source, inbox=inbox, events=events)

    acted = poller.tick()

    assert acted is True
    tasks = inbox.list()
    assert len(tasks) == 1
    assert tasks[0].data["title"] == "Fix bug"

    ingested = [(name, fields) for name, fields in events.events if name == "ingested"]
    assert len(ingested) == 1
    _, fields = ingested[0]
    assert fields["queue"] == "tasks"
    assert fields["task"]["id"] == tasks[0].id


def test_empty_poll_returns_false():
    source = MemoryTaskSource(clock=FakeClock())
    inbox = MemoryTaskQueue("tasks")
    poller = SourcePoller(source=source, inbox=inbox, events=MemoryEventSink())

    assert poller.tick() is False
    assert inbox.list() == []


def test_poll_that_raises_returns_false_and_emits_source_error():
    inbox = MemoryTaskQueue("tasks")
    events = MemoryEventSink()
    poller = SourcePoller(source=RaisingSource(), inbox=inbox, events=events)

    assert poller.tick() is False
    errors = [(name, fields) for name, fields in events.events if name == "source_error"]
    assert len(errors) == 1
    _, fields = errors[0]
    assert fields["source"] == "boom"
    assert "GitHub je dole" in fields["error"]
