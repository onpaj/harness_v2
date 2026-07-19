from dataclasses import replace

from harness.drivers.dummy_behavior import DummyBehavior
from harness.drivers.memory import FakeClock
from harness.models import Outcome, Task


def make_task(status: str) -> Task:
    return Task(
        id="tsk_1",
        workflow_template="default",
        created="2026-07-19T10:00:00Z",
        status=status,
    )


async def test_returns_done_and_waits():
    clock = FakeClock()
    behavior = DummyBehavior(clock=clock, delay=5.0)

    outcome = await behavior.run(make_task("design"))

    assert outcome is Outcome.DONE
    assert clock.slept == [5.0]


async def test_configured_step_asks_for_changes_only_once():
    behavior = DummyBehavior(
        clock=FakeClock(), delay=0.0, request_changes_once_at="review"
    )
    task = make_task("review")

    assert await behavior.run(task) is Outcome.REQUEST_CHANGES
    assert await behavior.run(task) is Outcome.DONE
    assert await behavior.run(task) is Outcome.DONE


async def test_other_steps_are_unaffected():
    behavior = DummyBehavior(
        clock=FakeClock(), delay=0.0, request_changes_once_at="review"
    )

    assert await behavior.run(make_task("design")) is Outcome.DONE


async def test_request_changes_is_per_task():
    behavior = DummyBehavior(
        clock=FakeClock(), delay=0.0, request_changes_once_at="review"
    )
    first = make_task("review")
    second = replace(first, id="tsk_2")

    assert await behavior.run(first) is Outcome.REQUEST_CHANGES
    assert await behavior.run(second) is Outcome.REQUEST_CHANGES
    assert await behavior.run(first) is Outcome.DONE
