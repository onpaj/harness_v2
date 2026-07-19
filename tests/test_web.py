"""Tests for the read-only dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agentharness.config import Config
from agentharness.dispatch.limits import ConcurrencyLimiter, RateLimitGate
from agentharness.models import RunRecord, Task
from agentharness.queue.filesystem import FilesystemQueue
from agentharness.store.runs import RunStore
from agentharness.web.app import create_app


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


@pytest.fixture()
def queue(cfg) -> FilesystemQueue:
    return FilesystemQueue(cfg.queues_dir)


@pytest.fixture()
def gate() -> RateLimitGate:
    return RateLimitGate(["rate limit"])


@pytest.fixture()
def limiter() -> ConcurrencyLimiter:
    return ConcurrencyLimiter(3)


@pytest.fixture()
def client(cfg, queue, store, limiter, gate) -> TestClient:
    return TestClient(create_app(cfg, queue, store, limiter, gate))


def make_task(task_id: str, agent: str = "planner", trace_id: str = "tr-1") -> Task:
    return Task(
        task_id=task_id,
        trace_id=trace_id,
        agent=agent,
        intent="do a thing",
        idempotency_key=f"key-{task_id}",
        created_at=_now(),
    )


def make_run(
    run_id: str,
    *,
    agent: str = "planner",
    trace_id: str = "tr-1",
    status: str = "ok",
    cost: float | None = 0.25,
    started_at: datetime | None = None,
) -> RunRecord:
    started = started_at or _now()
    return RunRecord(
        run_id=run_id,
        task_id=f"task-{run_id}",
        trace_id=trace_id,
        agent=agent,
        attempt=1,
        status=status,
        started_at=started,
        ended_at=started + timedelta(seconds=2),
        duration_ms=2000,
        total_cost_usd=cost,
        stdout_log=".harness/runs/tr-1/task-1/logs/stdout.log",
        stderr_log=".harness/runs/tr-1/task-1/logs/stderr.log",
    )


def add_run(store: RunStore, run: RunRecord) -> RunRecord:
    """Runs reference tasks by foreign key, so record the task row first."""
    store.record_task(make_task(run.task_id, agent=run.agent, trace_id=run.trace_id))
    store.record_run(run)
    return run


# --- index -------------------------------------------------------------------


def test_index_shows_queue_depths(client, queue):
    queue.enqueue(make_task("t1", agent="planner"))
    queue.enqueue(make_task("t2", agent="planner"))
    queue.enqueue(make_task("t3", agent="reviewer"))

    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "planner" in body
    assert "reviewer" in body
    # depth cells for both agents
    assert ">2<" in body.replace(" ", "").replace("\n", "")
    assert ">1<" in body.replace(" ", "").replace("\n", "")


def test_index_shows_24h_cost_and_recent_runs(client, store):
    add_run(store, make_run("run-1", cost=1.5))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "run-1" in resp.text
    assert "1.5" in resp.text


def test_paused_banner_only_when_gate_tripped(client, gate):
    assert "PAUSED" not in client.get("/").text
    gate.trip()
    assert "PAUSED" in client.get("/").text


# --- runs --------------------------------------------------------------------


def test_runs_lists_recorded_run(client, store):
    add_run(store, make_run("run-42"))
    resp = client.get("/runs")
    assert resp.status_code == 200
    assert "run-42" in resp.text


def test_run_detail_shows_agent_status_cost(client, store):
    add_run(store, make_run("run-7", agent="reviewer", status="failed", cost=0.75))
    resp = client.get("/runs/run-7")
    assert resp.status_code == 200
    assert "reviewer" in resp.text
    assert "failed" in resp.text
    assert "0.75" in resp.text
    assert "stdout.log" in resp.text


def test_run_detail_unknown_returns_404(client):
    assert client.get("/runs/unknown").status_code == 404


# --- traces ------------------------------------------------------------------


def test_trace_lists_runs_in_order(client, store):
    base = _now()
    add_run(store, make_run("run-a", started_at=base))
    add_run(store, make_run("run-b", started_at=base + timedelta(seconds=10)))
    add_run(store, make_run("run-c", started_at=base + timedelta(seconds=20)))
    add_run(store, make_run("run-other", trace_id="tr-2", started_at=base))

    resp = client.get("/traces/tr-1")
    assert resp.status_code == 200
    body = resp.text
    assert "run-other" not in body
    assert body.index("run-a") < body.index("run-b") < body.index("run-c")


def test_trace_unknown_is_empty_not_error(client):
    resp = client.get("/traces/nope")
    assert resp.status_code == 200


# --- api ---------------------------------------------------------------------


def test_api_snapshot_returns_snapshot_fields(client, queue, store, gate):
    queue.enqueue(make_task("t1", agent="planner"))
    add_run(store, make_run("run-1", cost=2.0))

    data = client.get("/api/snapshot").json()
    assert set(data) == {
        "queue_depths",
        "dead_depths",
        "active",
        "runs_24h",
        "failures_24h",
        "cost_24h",
        "paused",
    }
    assert data["queue_depths"]["planner"] == 1
    assert data["runs_24h"] == 1
    assert data["failures_24h"] == 0
    assert data["cost_24h"] == pytest.approx(2.0)
    assert data["paused"] is False


# --- read-only ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/runs", "/runs/x", "/traces/x", "/api/snapshot"])
@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_no_route_accepts_mutating_methods(client, path, method):
    resp = getattr(client, method)(path)
    assert resp.status_code == 405


def test_app_declares_only_get_routes(cfg, queue, store, limiter, gate):
    app = create_app(cfg, queue, store, limiter, gate)
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if methods is None:
            continue
        assert methods <= {"GET", "HEAD"}, f"{route.path} exposes {methods}"
