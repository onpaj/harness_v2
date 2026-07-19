from harness.drivers.fifo_strategy import FifoStrategy
from harness.models import Task


def make_task(task_id: str, created: str) -> Task:
    return Task(id=task_id, workflow_template="default", created=created)


def test_empty_list_selects_nothing():
    assert FifoStrategy().select([]) is None


def test_selects_oldest_by_created():
    tasks = [
        make_task("tsk_b", "2026-07-19T10:00:05Z"),
        make_task("tsk_a", "2026-07-19T10:00:01Z"),
    ]

    assert FifoStrategy().select(tasks).id == "tsk_a"


def test_ties_broken_by_id_for_determinism():
    tasks = [
        make_task("tsk_b", "2026-07-19T10:00:00Z"),
        make_task("tsk_a", "2026-07-19T10:00:00Z"),
    ]

    assert FifoStrategy().select(tasks).id == "tsk_a"
