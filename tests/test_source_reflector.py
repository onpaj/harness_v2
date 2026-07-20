"""SourceReflectorSink — překlad proudu eventů na projekci do zdroje."""

from harness.drivers.memory import FakeClock, MemoryTaskSource
from harness.drivers.source_reflector import SourceReflectorSink
from harness.models import Task


def _memory_task(source):
    source.submit("Fix bug")
    [task] = source.poll()
    return task


def test_dispatched_reports_progress_with_step_from_to():
    source = MemoryTaskSource(clock=FakeClock())
    task = _memory_task(source)
    reflector = SourceReflectorSink([source])

    reflector.emit(
        "dispatched",
        task_id=task.id,
        **{"from": None, "to": "development"},
        queue="development",
        task=task.to_dict(),
    )

    issue = task.data["source"]["issue"]
    assert source.states[issue] == [("progress", "development")]


def test_dispatched_falls_back_to_queue_when_to_missing():
    source = MemoryTaskSource(clock=FakeClock())
    task = _memory_task(source)
    reflector = SourceReflectorSink([source])

    reflector.emit("dispatched", task_id=task.id, queue="review", task=task.to_dict())

    issue = task.data["source"]["issue"]
    assert source.states[issue] == [("progress", "review")]


def test_finished_projects_finish_ok():
    source = MemoryTaskSource(clock=FakeClock())
    task = _memory_task(source)
    reflector = SourceReflectorSink([source])

    reflector.emit("finished", task_id=task.id, queue="done", task=task.to_dict())

    issue = task.data["source"]["issue"]
    assert source.states[issue] == [("finish", True)]


def test_failed_projects_finish_not_ok():
    source = MemoryTaskSource(clock=FakeClock())
    task = _memory_task(source)
    reflector = SourceReflectorSink([source])

    reflector.emit(
        "failed", task_id=task.id, reason="rozbil se git", queue="failed", task=task.to_dict()
    )

    issue = task.data["source"]["issue"]
    assert source.states[issue] == [("finish", False)]


def test_event_without_task_is_silent():
    source = MemoryTaskSource(clock=FakeClock())
    reflector = SourceReflectorSink([source])

    reflector.emit("dispatched", task_id="tsk_x", to="development")

    assert source.states == {}


def test_unknown_event_name_is_ignored():
    source = MemoryTaskSource(clock=FakeClock())
    task = _memory_task(source)
    reflector = SourceReflectorSink([source])

    reflector.emit("claimed", task_id=task.id, queue="development", task=task.to_dict())

    assert source.states == {}


def test_foreign_kind_task_is_ignored_by_adapter_guard():
    source = MemoryTaskSource(clock=FakeClock())  # kind == "memory"
    reflector = SourceReflectorSink([source])
    foreign = Task(
        id="tsk_x",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        data={"source": {"kind": "github", "issue": 7}},
    )

    reflector.emit("finished", task_id=foreign.id, queue="done", task=foreign.to_dict())

    assert source.states == {}
