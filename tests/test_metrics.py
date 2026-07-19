"""Tests for structured logging (obs.logging) and metrics (obs.metrics)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from agentharness.config import Config
from agentharness.dispatch.limits import ConcurrencyLimiter, RateLimitGate
from agentharness.models import RunRecord, Task
from agentharness.obs.logging import configure_logging, log_event
from agentharness.obs.metrics import (
    Snapshot,
    cost_by_agent,
    cost_by_trace,
    latency_percentiles,
    snapshot,
)
from agentharness.queue.filesystem import FilesystemQueue
from agentharness.store.runs import RunStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def cfg(home) -> Config:
    c = Config(home=home)
    c.ensure_dirs()
    return c


@pytest.fixture()
def store(cfg) -> RunStore:
    return RunStore(cfg.db_path)


def make_run(
    run_id: str,
    *,
    agent: str = "planner",
    trace_id: str = "tr-1",
    status: str = "ok",
    cost: float | None = 0.5,
    started_at: datetime | None = None,
    duration_ms: int | None = 1000,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        task_id=f"task-{run_id}",
        trace_id=trace_id,
        agent=agent,
        attempt=1,
        status=status,
        started_at=started_at or _now(),
        ended_at=(started_at or _now()) + timedelta(seconds=1),
        duration_ms=duration_ms,
        total_cost_usd=cost,
    )


def make_task(task_id: str, agent: str = "planner", trace_id: str = "tr-1") -> Task:
    return Task(
        task_id=task_id,
        trace_id=trace_id,
        agent=agent,
        intent="do a thing",
        idempotency_key=f"key-{task_id}",
        created_at=_now(),
    )


def add_run(store: RunStore, run: RunRecord) -> RunRecord:
    """Runs reference tasks by foreign key, so record the task row first."""
    store.record_task(make_task(run.task_id, agent=run.agent, trace_id=run.trace_id))
    store.record_run(run)
    return run


# --- logging -----------------------------------------------------------------


def test_configure_logging_creates_log_file(cfg):
    configure_logging(cfg.home)
    assert (cfg.home / "logs" / "harness.jsonl").parent.is_dir()


def test_log_event_writes_store_row_and_json_line(cfg, store):
    configure_logging(cfg.home)
    log_event(
        store,
        "run_started",
        trace_id="tr-1",
        task_id="task-1",
        run_id="run-1",
        agent="planner",
        extra="detail",
    )

    events = store.events_for_trace("tr-1")
    assert len(events) == 1
    assert events[0]["kind"] == "run_started"
    assert events[0]["run_id"] == "run-1"
    assert events[0]["data"]["extra"] == "detail"

    lines = (cfg.home / "logs" / "harness.jsonl").read_text().strip().splitlines()
    assert lines, "expected a JSON line on disk"
    payload = json.loads(lines[-1])
    assert payload["kind"] == "run_started"
    assert payload["trace_id"] == "tr-1"
    assert payload["extra"] == "detail"
    assert payload.get("timestamp")


def test_log_event_without_optional_fields(cfg, store):
    configure_logging(cfg.home)
    log_event(store, "harness_started")
    events = store.recent_events()
    assert events[0]["kind"] == "harness_started"


# --- cost --------------------------------------------------------------------


def test_cost_by_agent_sums_per_agent_within_window(store):
    now = _now()
    since = now - timedelta(hours=24)
    add_run(store, make_run("r1", agent="planner", cost=1.0, started_at=now))
    add_run(store, make_run("r2", agent="planner", cost=0.5, started_at=now))
    add_run(store, make_run("r3", agent="reviewer", cost=2.0, started_at=now))
    # outside the window
    old = now - timedelta(days=3)
    add_run(store, make_run("r4", agent="planner", cost=99.0, started_at=old))
    # null cost is ignored
    add_run(store, make_run("r5", agent="planner", cost=None, started_at=now))

    assert cost_by_agent(store, since) == {"planner": 1.5, "reviewer": 2.0}


def test_cost_by_agent_empty(store):
    assert cost_by_agent(store, _now() - timedelta(hours=1)) == {}


def test_cost_by_trace_sums_whole_trace(store):
    now = _now()
    add_run(store, make_run("r1", trace_id="tr-a", cost=1.25, started_at=now))
    add_run(store, make_run("r2", trace_id="tr-a", cost=0.75, started_at=now))
    add_run(store, make_run("r3", trace_id="tr-a", cost=None, started_at=now))
    add_run(store, make_run("r4", trace_id="tr-b", cost=9.0, started_at=now))

    assert cost_by_trace(store, "tr-a") == 2.0
    assert cost_by_trace(store, "tr-missing") == 0.0


# --- latency -----------------------------------------------------------------


def test_latency_percentiles_known_set(store):
    now = _now()
    for i, ms in enumerate([100, 200, 300, 400, 500]):
        add_run(store, make_run(f"r{i}", duration_ms=ms, started_at=now))

    pct = latency_percentiles(store, now - timedelta(hours=1))
    assert pct["p50"] == pytest.approx(300.0)
    assert pct["p95"] == pytest.approx(480.0)


def test_latency_percentiles_ignores_old_and_null(store):
    now = _now()
    add_run(store, make_run("r1", duration_ms=100, started_at=now))
    add_run(store, make_run("r2", duration_ms=None, started_at=now))
    old = now - timedelta(days=2)
    add_run(store, make_run("r3", duration_ms=99999, started_at=old))

    pct = latency_percentiles(store, now - timedelta(hours=1))
    assert pct == {"p50": 100.0, "p95": 100.0}


def test_latency_percentiles_empty(store):
    assert latency_percentiles(store, _now()) == {"p50": 0.0, "p95": 0.0}


# --- snapshot ----------------------------------------------------------------


def test_snapshot_reports_depths_counts_and_paused(cfg, store):
    queue = FilesystemQueue(cfg.queues_dir)
    queue.enqueue(make_task("t1", agent="planner"))
    queue.enqueue(make_task("t2", agent="planner"))
    dead = make_task("t3", agent="reviewer")
    queue.enqueue(dead)
    queue.lease("reviewer", 60)
    queue.dead_letter(dead, "boom")

    now = _now()
    add_run(store, make_run("r1", agent="planner", cost=1.0, started_at=now))
    add_run(
        store,
        make_run("r2", agent="planner", status="failed", cost=0.5, started_at=now),
    )
    old = now - timedelta(days=2)
    add_run(store, make_run("r3", agent="planner", cost=50.0, started_at=old))

    limiter = ConcurrencyLimiter(3)
    gate = RateLimitGate(["rate limit"])

    snap = snapshot(cfg, queue, store, limiter, gate)
    assert isinstance(snap, Snapshot)
    assert snap.queue_depths["planner"] == 2
    assert snap.queue_depths["reviewer"] == 0
    assert snap.dead_depths["reviewer"] == 1
    assert snap.dead_depths["planner"] == 0
    assert snap.active == {"planner": 0, "reviewer": 0}
    assert snap.runs_24h == 2
    assert snap.failures_24h == 1
    assert snap.cost_24h == pytest.approx(1.5)
    assert snap.paused is False

    gate.trip()
    assert snapshot(cfg, queue, store, limiter, gate).paused is True
