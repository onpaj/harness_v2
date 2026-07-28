"""RetentionReconciler — retires terminal tasks that settled long enough ago."""

from harness.drivers.memory import FakeClock, MemoryEventSink, MemoryTaskQueue
from harness.models import ARCHIVED, HistoryEntry, Task
from harness.retention_reconciler import DEFAULT_RETENTION_DAYS, RetentionReconciler

NOW = "2026-07-28T12:00:00Z"


def _task(task_id="tsk_1", *, settled=None, created="2026-07-01T10:00:00Z", status="done"):
    """A terminal task. `settled` becomes the last history entry's `at`."""
    history = ()
    if settled is not None:
        history = (
            HistoryEntry(
                at=settled, actor="consumer", from_step="plan", to_step=None, outcome="done"
            ),
        )
    return Task(
        id=task_id,
        workflow_template=None,
        created=created,
        status=status,
        history=history,
    )


def _build(*queues, days=2):
    archived = MemoryTaskQueue("archived")
    events = MemoryEventSink()
    reconciler = RetentionReconciler(
        queues=list(queues),
        archived=archived,
        days=days,
        events=events,
        clock=FakeClock(NOW),
    )
    return reconciler, archived, events


def test_task_settled_longer_ago_than_the_window_is_archived():
    done = MemoryTaskQueue("done")
    reconciler, archived, events = _build(done)
    done.put(_task(settled="2026-07-25T12:00:00Z"))  # 3 days

    assert reconciler.tick() is True
    assert done.list() == []
    assert [t.id for t in archived.list()] == ["tsk_1"]
    assert "archived" in events.names()


def test_task_settled_inside_the_window_stays_on_the_board():
    done = MemoryTaskQueue("done")
    reconciler, archived, events = _build(done)
    done.put(_task(settled="2026-07-27T12:00:00Z"))  # 1 day

    assert reconciler.tick() is False
    assert [t.id for t in done.list()] == ["tsk_1"]
    assert archived.list() == []
    assert "archived" not in events.names()


def test_age_is_measured_from_the_last_history_entry_not_creation():
    """Created a month ago, settled today: it must stay."""
    done = MemoryTaskQueue("done")
    reconciler, archived, _ = _build(done)
    done.put(_task(created="2026-06-25T10:00:00Z", settled="2026-07-28T09:00:00Z"))

    assert reconciler.tick() is False
    assert [t.id for t in done.list()] == ["tsk_1"]
    assert archived.list() == []


def test_empty_history_falls_back_to_created():
    done = MemoryTaskQueue("done")
    reconciler, archived, _ = _build(done)
    done.put(_task(created="2026-07-01T10:00:00Z"))  # no history, long past

    assert reconciler.tick() is True
    assert [t.id for t in archived.list()] == ["tsk_1"]


def test_unparseable_timestamp_leaves_the_task_alone():
    """Malformed data must not silently vanish off the board."""
    done = MemoryTaskQueue("done")
    reconciler, archived, _ = _build(done)
    done.put(_task(created="not-a-timestamp"))

    assert reconciler.tick() is False
    assert [t.id for t in done.list()] == ["tsk_1"]
    assert archived.list() == []


def test_it_sweeps_every_queue_it_was_given():
    done = MemoryTaskQueue("done")
    failed = MemoryTaskQueue("failed")
    healed = MemoryTaskQueue("healed")
    reconciler, archived, _ = _build(done, failed, healed)
    done.put(_task("tsk_d", settled="2026-07-20T12:00:00Z", status="done"))
    failed.put(_task("tsk_f", settled="2026-07-20T12:00:00Z", status="failed"))
    healed.put(_task("tsk_h", settled="2026-07-20T12:00:00Z", status="healed"))

    assert reconciler.tick() is True
    assert sorted(t.id for t in archived.list()) == ["tsk_d", "tsk_f", "tsk_h"]


def test_the_archived_task_carries_archived_status_and_an_audit_entry():
    done = MemoryTaskQueue("done")
    reconciler, archived, events = _build(done)
    done.put(_task(settled="2026-07-20T12:00:00Z"))

    reconciler.tick()

    task = archived.list()[0]
    assert task.status == ARCHIVED
    assert task.lock_id is None
    entry = task.history[-1]
    assert entry.actor == "retention"
    assert entry.at == NOW
    assert entry.from_step == "done"
    assert "retention" in (entry.reason or "")

    name, fields = next(pair for pair in events.events if pair[0] == "archived")
    assert fields["task_id"] == "tsk_1"
    assert fields["queue"] == "archived"
    assert fields["task"]["status"] == ARCHIVED


def test_a_lost_claim_race_is_skipped_not_fatal():
    class LosesEveryClaim(MemoryTaskQueue):
        def claim(self, task, lock_id):
            return None

    done = LosesEveryClaim("done")
    reconciler, archived, events = _build(done)
    done.put(_task(settled="2026-07-20T12:00:00Z"))

    assert reconciler.tick() is False
    assert archived.list() == []
    assert "archived" not in events.names()


def test_days_zero_archives_every_terminal_task_on_the_next_sweep():
    done = MemoryTaskQueue("done")
    reconciler, archived, _ = _build(done, days=0)
    done.put(_task(settled=NOW))

    assert reconciler.tick() is True
    assert [t.id for t in archived.list()] == ["tsk_1"]


def test_the_default_window_is_two_days():
    assert DEFAULT_RETENTION_DAYS == 2
