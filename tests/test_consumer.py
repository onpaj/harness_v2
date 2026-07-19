from harness.consumer import Consumer
from harness.drivers.memory import (
    FakeClock,
    MemoryEventSink,
    MemoryTaskQueue,
    ScriptedBehavior,
)
from harness.models import FAILED, Outcome, Task
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
    _, fields = next(item for item in events.events if item[0] == "consumed")
    assert fields["task_id"] == "tsk_1"
    assert fields["step"] == "design"
    assert fields["outcome"] == "done"


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


async def test_behavior_exception_task_carries_terminal_failed_status():
    """Task nahozený výjimkou z behavior musí ve failed/ nést terminální
    status 'failed', ne zůstat na starém kroku ('design') — jinak by
    status lhal stejně jako v Finding 1 na dispatcher straně."""
    consumer, _, _, failed, _ = build(ExplodingBehavior(), make_task())

    await consumer.tick()

    assert failed.list()[0].status == FAILED


async def test_invalid_outcome_lands_in_failed():
    consumer, _, _, failed, _ = build(BogusBehavior(), make_task())

    await consumer.tick()

    assert "neco jineho" in failed.list()[0].history[-1].reason


async def test_invalid_outcome_task_carries_terminal_failed_status():
    consumer, _, _, failed, _ = build(BogusBehavior(), make_task())

    await consumer.tick()

    assert failed.list()[0].status == FAILED


# test_consumer_has_no_branch_on_outcome_value byl nahrazen AST-based testem
# tests/test_architecture.py::test_consumer_has_no_branch_on_outcome_value —
# viz komentář tam pro důvod (string-matching přes inspect.getsource nechytal
# `if outcome == "done":`, aliasovaný import, ani větev v modulové funkci).


async def test_claimed_event_is_emitted_before_behavior_runs():
    consumer, _, _, _, events = build(ScriptedBehavior(), make_task())

    await consumer.tick()

    names = events.names()
    assert names.index("claimed") < names.index("consumed")
    _, fields = next(item for item in events.events if item[0] == "claimed")
    assert fields["queue"] == "design"
    assert fields["task"]["lockId"] is not None


async def test_consumed_event_carries_task_snapshot_and_queue():
    consumer, _, _, _, events = build(ScriptedBehavior(), make_task())

    await consumer.tick()

    _, fields = next(item for item in events.events if item[0] == "consumed")
    assert fields["queue"] == "design"
    assert fields["task"]["lastOutcome"] == "done"
    assert fields["task"]["lockId"] is None


async def test_consumer_failure_event_carries_failed_queue():
    consumer, _, _, _, events = build(ExplodingBehavior(), make_task())

    await consumer.tick()

    _, fields = next(item for item in events.events if item[0] == "failed")
    assert fields["queue"] == "failed"
    assert fields["task"]["id"] == "tsk_1"
