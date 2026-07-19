import pytest

from harness.drivers.memory import (
    FakeClock,
    MemoryEventSink,
    MemoryTaskQueue,
    MemoryWorkflowRepository,
)
from harness.models import Task, Transition, Workflow
from harness.ports.workflows import WorkflowNotFound


def make_task(task_id="tsk_1") -> Task:
    return Task(id=task_id, workflow_template="default", created="2026-07-19T10:00:00Z")


def test_put_then_list():
    queue = MemoryTaskQueue("tasks")
    task = make_task()

    queue.put(task)

    assert queue.list() == [task]


def test_claim_removes_from_list_and_stamps_lock():
    queue = MemoryTaskQueue("tasks")
    queue.put(make_task())

    claimed = queue.claim(queue.list()[0], "lck_1")

    assert claimed is not None
    assert claimed.lock_id == "lck_1"
    assert queue.list() == []


def test_claim_twice_loses_the_race():
    queue = MemoryTaskQueue("tasks")
    queue.put(make_task())
    task = queue.list()[0]
    queue.claim(task, "lck_1")

    assert queue.claim(task, "lck_2") is None


def test_transfer_moves_between_queues():
    source = MemoryTaskQueue("tasks")
    destination = MemoryTaskQueue("design")
    source.put(make_task())
    claimed = source.claim(source.list()[0], "lck_1")

    source.transfer(claimed, destination)

    assert source.list() == []
    assert destination.list() == [claimed]


def test_recover_returns_claimed_tasks_and_clears_lock():
    queue = MemoryTaskQueue("tasks")
    queue.put(make_task())
    queue.claim(queue.list()[0], "lck_1")

    recovered = queue.recover()

    assert recovered == 1
    assert queue.list()[0].lock_id is None


def test_workflow_repository_get_and_miss():
    workflow = Workflow(
        name="default",
        start="plan",
        transitions=(Transition(from_step="plan", on="done", to_step="end"),),
    )
    repository = MemoryWorkflowRepository({"default": workflow})

    assert repository.get("default") == workflow
    with pytest.raises(WorkflowNotFound):
        repository.get("missing")


def test_event_sink_records():
    sink = MemoryEventSink()

    sink.emit("dispatched", task_id="tsk_1", to="design")

    assert sink.events == [("dispatched", {"task_id": "tsk_1", "to": "design"})]


async def test_fake_clock_does_not_really_sleep():
    clock = FakeClock("2026-07-19T10:00:00Z")

    await clock.sleep(5.0)

    assert clock.now() == "2026-07-19T10:00:00Z"
    assert clock.slept == [5.0]
