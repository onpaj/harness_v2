from dataclasses import replace

from harness.consumer import Consumer
from harness.drivers.memory import (
    FakeClock,
    MemoryEventSink,
    MemoryTaskQueue,
    ScriptedBehavior,
)
from harness.models import DONE, FAILED, REQUEST_CHANGES, BehaviorResult, Task
from harness.ports.behavior import ConsumerBehavior
from harness.ports.strategy import EnqueueStrategy


class DataBehavior(ConsumerBehavior):
    """Returns a fixed BehaviorResult.data payload — for testing the consumer's merge."""

    def __init__(self, data: dict) -> None:
        self._data = data

    async def run(self, task: Task) -> BehaviorResult:
        return BehaviorResult(DONE, "done", data=self._data)


class FirstStrategy(EnqueueStrategy):
    def select(self, tasks):
        return tasks[0] if tasks else None


class ExplodingBehavior(ConsumerBehavior):
    async def run(self, task):
        raise RuntimeError("behavior blew up")


class BogusBehavior(ConsumerBehavior):
    async def run(self, task):
        return "something else"


class EmptyOutcomeBehavior(ConsumerBehavior):
    """Returns a well-formed BehaviorResult whose outcome is an empty string —
    a non-string/empty outcome is still rejected under the open string type."""

    async def run(self, task):
        return BehaviorResult("", "no outcome given")


class DataBehavior(ConsumerBehavior):
    def __init__(self, data: dict) -> None:
        self._data = data

    async def run(self, task):
        return BehaviorResult(DONE, "done with data", data=self._data)


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
    behavior = ScriptedBehavior({"design": [REQUEST_CHANGES]})
    consumer, _, inbox, _, _ = build(behavior, make_task())

    await consumer.tick()

    assert inbox.list()[0].last_outcome == "request_changes"


async def test_consumer_never_changes_status():
    behavior = ScriptedBehavior({"design": [REQUEST_CHANGES]})
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


async def test_consumer_records_behavior_summary():
    """The summary from BehaviorResult propagates into both history and the event."""
    consumer, _, inbox, _, events = build(ScriptedBehavior(), make_task())

    await consumer.tick()

    assert inbox.list()[0].history[-1].summary == "design: done"
    _, fields = next(item for item in events.events if item[0] == "consumed")
    assert fields["summary"] == "design: done"


async def test_behavior_exception_lands_in_failed():
    consumer, _, inbox, failed, events = build(ExplodingBehavior(), make_task())

    assert await consumer.tick() is True

    assert inbox.list() == []
    assert "behavior blew up" in failed.list()[0].history[-1].reason
    assert "failed" in events.names()


async def test_behavior_exception_task_carries_terminal_failed_status():
    """A task raised by a behavior exception must carry terminal status
    'failed' in failed/, not stay on the old step ('design') — otherwise
    status would lie just as in Finding 1 on the dispatcher side."""
    consumer, _, _, failed, _ = build(ExplodingBehavior(), make_task())

    await consumer.tick()

    assert failed.list()[0].status == FAILED


async def test_invalid_outcome_lands_in_failed():
    consumer, _, _, failed, _ = build(BogusBehavior(), make_task())

    await consumer.tick()

    assert "something else" in failed.list()[0].history[-1].reason


async def test_invalid_outcome_task_carries_terminal_failed_status():
    consumer, _, _, failed, _ = build(BogusBehavior(), make_task())

    await consumer.tick()

    assert failed.list()[0].status == FAILED


async def test_empty_string_outcome_lands_in_failed():
    """The open-string outcome guard is a shape check — non-empty string —
    not an enum membership check; an empty outcome is still invalid."""
    consumer, _, _, failed, _ = build(EmptyOutcomeBehavior(), make_task())

    await consumer.tick()

    assert failed.list()[0].status == FAILED


# test_consumer_has_no_branch_on_outcome_value was replaced by the AST-based test
# tests/test_architecture.py::test_consumer_has_no_branch_on_outcome_value —
# see the comment there for the reason (string-matching via inspect.getsource
# missed `if outcome == "done":`, an aliased import, and a branch in a module function).


async def test_result_data_is_merged_into_task_data():
    consumer, _, inbox, _, _ = build(
        DataBehavior({"pr": {"number": 1, "branch": "harness/tsk_1"}}), make_task()
    )

    await consumer.tick()

    assert inbox.list()[0].data == {"pr": {"number": 1, "branch": "harness/tsk_1"}}


async def test_result_data_merge_preserves_existing_keys():
    task = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="design",
        data={"title": "add rate limiting"},
    )
    consumer, _, inbox, _, _ = build(DataBehavior({"pr": {"number": 1}}), task)

    await consumer.tick()

    updated = inbox.list()[0].data
    assert updated["title"] == "add rate limiting"
    assert updated["pr"] == {"number": 1}


async def test_no_result_data_leaves_task_data_untouched():
    task = Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status="design",
        data={"title": "add rate limiting"},
    )
    consumer, _, inbox, _, _ = build(ScriptedBehavior(), task)

    await consumer.tick()

    assert inbox.list()[0].data == {"title": "add rate limiting"}


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


async def test_behavior_result_data_is_merged_into_task_data():
    consumer, _, inbox, _, _ = build(DataBehavior({"pr": {"number": 45}}), make_task())

    await consumer.tick()

    assert inbox.list()[0].data == {"pr": {"number": 45}}


async def test_behavior_result_data_merge_preserves_existing_keys():
    task = replace(make_task(), data={"source": {"kind": "github"}})
    consumer, _, inbox, _, _ = build(DataBehavior({"pr": {"number": 45}}), task)

    await consumer.tick()

    assert inbox.list()[0].data == {
        "source": {"kind": "github"},
        "pr": {"number": 45},
    }


async def test_behavior_result_without_data_leaves_task_data_untouched():
    task = replace(make_task(), data={"source": {"kind": "github"}})
    consumer, _, inbox, _, _ = build(ScriptedBehavior(), task)

    await consumer.tick()

    assert inbox.list()[0].data == {"source": {"kind": "github"}}


async def test_consumer_failure_event_carries_failed_queue():
    consumer, _, _, _, events = build(ExplodingBehavior(), make_task())

    await consumer.tick()

    _, fields = next(item for item in events.events if item[0] == "failed")
    assert fields["queue"] == "failed"
    assert fields["task"]["id"] == "tsk_1"
