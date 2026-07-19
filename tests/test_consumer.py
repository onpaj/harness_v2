import inspect

from harness.consumer import Consumer
from harness.drivers.memory import (
    FakeClock,
    MemoryEventSink,
    MemoryTaskQueue,
    ScriptedBehavior,
)
from harness.models import Outcome, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.strategy import EnqueueStrategy


class FirstStrategy(EnqueueStrategy):
    def select(self, tasks):
        return tasks[0] if tasks else None


class ExplodingBehavior(ConsumerBehavior):
    async def run(self, task):
        raise RuntimeError("behavior praskl")


class BogusBehavior(ConsumerBehavior):
    async def run(self, task):
        return "neco jineho"


def build(behavior, task: Task | None = None):
    queue = MemoryTaskQueue("design")
    inbox = MemoryTaskQueue("tasks")
    failed = MemoryTaskQueue("failed")
    events = MemoryEventSink()
    consumer = Consumer(
        step="design",
        queue=queue,
        inbox=inbox,
        failed=failed,
        behavior=behavior,
        strategy=FirstStrategy(),
        events=events,
        clock=FakeClock(),
    )
    if task is not None:
        queue.put(task)
    return consumer, queue, inbox, failed, events


def make_task() -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="design",
    )


async def test_tick_on_empty_queue_does_nothing():
    consumer, *_ = build(ScriptedBehavior())

    assert await consumer.tick() is False


async def test_done_outcome_returns_task_to_inbox():
    consumer, queue, inbox, _, events = build(ScriptedBehavior(), make_task())

    assert await consumer.tick() is True

    assert queue.list() == []
    returned = inbox.list()[0]
    assert returned.last_outcome == "done"
    assert returned.lock_id is None
    assert ("consumed", {"task_id": "tsk_1", "step": "design", "outcome": "done"}) in events.events


async def test_request_changes_outcome_is_written_verbatim():
    behavior = ScriptedBehavior({"design": [Outcome.REQUEST_CHANGES]})
    consumer, _, inbox, _, _ = build(behavior, make_task())

    await consumer.tick()

    assert inbox.list()[0].last_outcome == "request_changes"


async def test_consumer_never_changes_status():
    behavior = ScriptedBehavior({"design": [Outcome.REQUEST_CHANGES]})
    consumer, _, inbox, _, _ = build(behavior, make_task())

    await consumer.tick()

    assert inbox.list()[0].status == "design"


async def test_consumer_appends_history_without_target():
    consumer, _, inbox, _, _ = build(ScriptedBehavior(), make_task())

    await consumer.tick()

    entry = inbox.list()[0].history[-1]
    assert entry.actor == "consumer:design"
    assert entry.from_step == "design"
    assert entry.to_step is None
    assert entry.outcome == "done"


async def test_behavior_exception_lands_in_failed():
    consumer, _, inbox, failed, events = build(ExplodingBehavior(), make_task())

    assert await consumer.tick() is True

    assert inbox.list() == []
    assert "behavior praskl" in failed.list()[0].history[-1].reason
    assert "failed" in events.names()


async def test_invalid_outcome_lands_in_failed():
    consumer, _, _, failed, _ = build(BogusBehavior(), make_task())

    await consumer.tick()

    assert "neco jineho" in failed.list()[0].history[-1].reason


def test_consumer_has_no_branch_on_outcome_value():
    """Rozhodování patří do ConsumerBehavior, ne sem."""
    source = inspect.getsource(Consumer)

    assert "Outcome.DONE" not in source
    assert "Outcome.REQUEST_CHANGES" not in source
    assert "request_changes" not in source
