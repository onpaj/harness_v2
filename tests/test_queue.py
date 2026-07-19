"""Behavioural tests for the durable filesystem queue."""

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentharness.models import Task
from agentharness.queue import FilesystemQueue, Queue


def make_task(
    task_id: str = "t_1",
    *,
    agent: str = "coder",
    priority: int = 5,
    idempotency_key: str | None = None,
    created_at: datetime | None = None,
    attempt: int = 1,
    trace_id: str = "tr_1",
) -> Task:
    return Task(
        task_id=task_id,
        trace_id=trace_id,
        agent=agent,
        intent="do a thing",
        idempotency_key=idempotency_key if idempotency_key is not None else f"key-{task_id}",
        priority=priority,
        attempt=attempt,
        created_at=created_at or datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture()
def q(tmp_path: Path) -> FilesystemQueue:
    return FilesystemQueue(tmp_path / "queues")


def test_filesystem_queue_is_a_queue():
    assert issubclass(FilesystemQueue, Queue)


def test_enqueue_then_depth_is_one(q):
    assert q.enqueue(make_task()) is True
    assert q.depth("coder") == 1


def test_lease_returns_task_and_depth_drops(q):
    task = make_task()
    q.enqueue(task)

    leased = q.lease("coder", visibility_timeout=60)

    assert leased is not None
    assert leased.task_id == task.task_id
    assert leased.intent == task.intent
    assert q.depth("coder") == 0


def test_lease_on_empty_queue_returns_none(q):
    assert q.lease("coder", visibility_timeout=60) is None


def test_ack_removes_the_processing_file(q, tmp_path):
    q.enqueue(make_task())
    leased = q.lease("coder", visibility_timeout=60)

    processing = tmp_path / "queues" / "coder" / "processing"
    assert list(processing.iterdir()) != []

    q.ack(leased)

    assert list(processing.iterdir()) == []


def test_duplicate_idempotency_key_is_rejected(q):
    first = make_task("t_1", idempotency_key="same")
    second = make_task("t_2", idempotency_key="same")

    assert q.enqueue(first) is True
    assert q.enqueue(second) is False
    assert q.depth("coder") == 1


def test_higher_priority_leases_first_even_when_enqueued_later(q):
    low = make_task(
        "t_low",
        priority=9,
        created_at=datetime(2026, 7, 19, 10, 0, 0, tzinfo=timezone.utc),
    )
    high = make_task(
        "t_high",
        priority=1,
        created_at=datetime(2026, 7, 19, 11, 0, 0, tzinfo=timezone.utc),
    )
    q.enqueue(low)
    q.enqueue(high)

    assert q.lease("coder", visibility_timeout=60).task_id == "t_high"
    assert q.lease("coder", visibility_timeout=60).task_id == "t_low"


def test_equal_priority_leases_fifo_by_creation_time(q):
    older = make_task("t_older", created_at=datetime(2026, 7, 19, 10, 0, 0, tzinfo=timezone.utc))
    newer = make_task("t_newer", created_at=datetime(2026, 7, 19, 11, 0, 0, tzinfo=timezone.utc))
    q.enqueue(newer)
    q.enqueue(older)

    assert q.lease("coder", visibility_timeout=60).task_id == "t_older"
    assert q.lease("coder", visibility_timeout=60).task_id == "t_newer"


def test_nack_requeues_with_attempt_incremented(q):
    q.enqueue(make_task(attempt=1))
    leased = q.lease("coder", visibility_timeout=60)

    q.nack(leased)

    assert q.depth("coder") == 1
    again = q.lease("coder", visibility_timeout=60)
    assert again.attempt == 2


def test_nack_without_requeue_drops_the_task(q, tmp_path):
    q.enqueue(make_task())
    leased = q.lease("coder", visibility_timeout=60)

    q.nack(leased, requeue=False)

    assert q.depth("coder") == 0
    assert list((tmp_path / "queues" / "coder" / "processing").iterdir()) == []


def test_nack_with_delay_lands_in_delayed_and_is_not_leasable_until_promoted(q, tmp_path, monkeypatch):
    q.enqueue(make_task())
    leased = q.lease("coder", visibility_timeout=60)

    q.nack(leased, delay_seconds=60)

    delayed = tmp_path / "queues" / "coder" / "delayed"
    assert len(list(delayed.iterdir())) == 1
    assert q.lease("coder", visibility_timeout=60) is None

    # Promoting with a "now" before the ready time does nothing.
    assert q.promote_delayed(now=0.0) == []
    assert q.lease("coder", visibility_timeout=60) is None

    future = (datetime.now(timezone.utc) + timedelta(seconds=3600)).timestamp()
    promoted = q.promote_delayed(now=future)

    assert [t.task_id for t in promoted] == ["t_1"]
    assert list(delayed.iterdir()) == []
    assert q.lease("coder", visibility_timeout=60) is not None


def test_dead_letter_writes_task_and_reason_and_shows_in_list_dead(q, tmp_path):
    q.enqueue(make_task())
    leased = q.lease("coder", visibility_timeout=60)

    q.dead_letter(leased, "exploded three times")

    dead_dir = tmp_path / "queues" / "coder" / "dead"
    assert (dead_dir / "t_1.json").exists()
    assert (dead_dir / "t_1.reason.txt").read_text() == "exploded three times"
    assert list((tmp_path / "queues" / "coder" / "processing").iterdir()) == []

    dead = q.list_dead("coder")
    assert [t.task_id for t in dead] == ["t_1"]


def test_list_dead_is_empty_for_unknown_agent(q):
    assert q.list_dead("nobody") == []


def test_replay_dead_moves_back_to_pending(q, tmp_path):
    q.enqueue(make_task())
    leased = q.lease("coder", visibility_timeout=60)
    q.dead_letter(leased, "nope")

    assert q.replay_dead("coder", "t_1") is True
    assert q.depth("coder") == 1
    assert q.list_dead("coder") == []
    assert not (tmp_path / "queues" / "coder" / "dead" / "t_1.reason.txt").exists()
    assert q.lease("coder", visibility_timeout=60).task_id == "t_1"


def test_replay_dead_returns_false_when_absent(q):
    assert q.replay_dead("coder", "nope") is False


def test_reclaim_expired_returns_expired_leases_and_leaves_fresh_ones(q):
    q.enqueue(make_task("t_expired", idempotency_key="a"))
    q.enqueue(make_task("t_fresh", idempotency_key="b"))
    first = q.lease("coder", visibility_timeout=1)
    second = q.lease("coder", visibility_timeout=10_000)
    leased_ids = {first.task_id, second.task_id}
    assert leased_ids == {"t_expired", "t_fresh"}

    # Nothing is expired right now.
    assert q.reclaim_expired(now=datetime.now(timezone.utc).timestamp()) == []

    later = datetime.now(timezone.utc).timestamp() + 60
    reclaimed = q.reclaim_expired(now=later)

    assert [t.task_id for t in reclaimed] == [first.task_id]
    assert q.depth("coder") == 1
    assert q.lease("coder", visibility_timeout=60).task_id == first.task_id


def test_reclaim_expired_scans_all_agents(q):
    q.enqueue(make_task("t_a", agent="alpha", idempotency_key="a"))
    q.enqueue(make_task("t_b", agent="beta", idempotency_key="b"))
    q.lease("alpha", visibility_timeout=1)
    q.lease("beta", visibility_timeout=1)

    later = datetime.now(timezone.utc).timestamp() + 60
    reclaimed = q.reclaim_expired(now=later)

    assert sorted(t.task_id for t in reclaimed) == ["t_a", "t_b"]


def test_agents_with_work_lists_only_agents_with_ready_work(q):
    q.enqueue(make_task("t_a", agent="alpha", idempotency_key="a"))
    q.enqueue(make_task("t_b", agent="beta", idempotency_key="b"))
    q.enqueue(make_task("t_c", agent="gamma", idempotency_key="c"))

    # beta's task is leased -> no pending work left.
    q.lease("beta", visibility_timeout=600)
    # gamma's task is delayed far into the future -> not ready.
    gamma = q.lease("gamma", visibility_timeout=600)
    q.nack(gamma, delay_seconds=3600)

    assert q.agents_with_work() == ["alpha"]


def test_agents_with_work_includes_agents_whose_delay_has_elapsed(q):
    q.enqueue(make_task("t_a", agent="alpha", idempotency_key="a"))
    leased = q.lease("alpha", visibility_timeout=600)
    q.nack(leased, delay_seconds=0.001)

    import time

    time.sleep(0.05)
    assert q.agents_with_work() == ["alpha"]


def test_depth_is_zero_for_unknown_agent(q):
    assert q.depth("nobody") == 0


def test_concurrent_leases_never_hand_out_the_same_task(tmp_path):
    q = FilesystemQueue(tmp_path / "queues")
    total = 40
    for i in range(total):
        q.enqueue(make_task(f"t_{i:03d}", idempotency_key=f"k{i}"))

    leased: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def worker():
        start.wait()
        while True:
            task = q.lease("coder", visibility_timeout=600)
            if task is None:
                return
            with lock:
                leased.append(task.task_id)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(leased) == total
    assert len(set(leased)) == total
    assert q.depth("coder") == 0
