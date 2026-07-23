"""Trigger — a TaskSource that produces tasks but reflects nothing outward."""

import pytest

from harness.models import Task
from harness.ports.source import FinishResult, Progress, TaskSource, Trigger


class EmptyTrigger(Trigger):
    """A minimal trigger: it only knows how to poll (and polls nothing)."""

    kind = "empty"

    def poll(self):
        return []


def _task():
    return Task(id="tsk_1", created="2026-07-22T10:00:00Z")


def test_trigger_subclass_implementing_only_poll_instantiates():
    trigger = EmptyTrigger()

    assert isinstance(trigger, Trigger)
    assert isinstance(trigger, TaskSource)
    assert trigger.poll() == []


def test_report_progress_is_a_noop():
    trigger = EmptyTrigger()

    assert trigger.report_progress(_task(), Progress("x")) is None


def test_finish_is_a_noop():
    trigger = EmptyTrigger()

    assert trigger.finish(_task(), FinishResult(ok=True)) is None


def test_trigger_itself_stays_abstract():
    # `poll` is still abstract, so `Trigger` cannot be instantiated directly.
    with pytest.raises(TypeError):
        Trigger()


def test_subclass_without_poll_stays_abstract():
    class NoPoll(Trigger):
        kind = "nopoll"

    with pytest.raises(TypeError):
        NoPoll()
