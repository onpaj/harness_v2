"""Tests for the SQLite run store (Task 5)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from agentharness.models import RunRecord, ScheduleDef, Task, TaskArtifacts
from agentharness.store.db import SCHEMA_VERSION, connect
from agentharness.store.runs import RunStore

UTC = timezone.utc


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "harness.db"


@pytest.fixture()
def store(db_path):
    return RunStore(db_path)


def make_task(
    task_id: str = "t1",
    *,
    trace_id: str = "tr1",
    parent_task_id: str | None = None,
    agent: str = "coder",
    repo: str | None = "app",
    intent: str = "do a thing",
    **kw,
) -> Task:
    return Task(
        task_id=task_id,
        trace_id=trace_id,
        parent_task_id=parent_task_id,
        agent=agent,
        repo=repo,
        intent=intent,
        idempotency_key=kw.pop("idempotency_key", f"idem-{task_id}"),
        created_at=kw.pop("created_at", datetime(2026, 7, 19, 12, 0, tzinfo=UTC)),
        **kw,
    )


def make_run(
    run_id: str = "r1",
    *,
    task_id: str = "t1",
    trace_id: str = "tr1",
    agent: str = "coder",
    status: str = "ok",
    branch: str | None = None,
    started_at: datetime | None = None,
    **kw,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        task_id=task_id,
        trace_id=trace_id,
        agent=agent,
        attempt=kw.pop("attempt", 1),
        status=status,
        branch=branch,
        started_at=started_at or datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        **kw,
    )


# --- schema ------------------------------------------------------------------


def test_connect_creates_schema_on_fresh_file(db_path):
    conn = connect(db_path)
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"tasks", "runs", "handoffs", "schedules", "traces", "events"} <= names
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_connect_enables_wal_and_foreign_keys(db_path):
    conn = connect(db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_connect_is_idempotent(db_path):
    c1 = connect(db_path)
    c1.execute(
        "INSERT INTO traces(trace_id, merged_ref, merged_at) VALUES ('tr1', NULL, NULL)"
    )
    c1.commit()
    c1.close()

    c2 = connect(db_path)
    assert c2.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert c2.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 1
    c2.close()


def test_expected_indexes_exist(db_path):
    conn = connect(db_path)
    idx = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    for expected in (
        "idx_tasks_trace",
        "idx_tasks_status",
        "idx_runs_trace",
        "idx_events_trace",
        "idx_events_ts",
    ):
        assert expected in idx
    conn.close()


def test_connect_creates_parent_directory(tmp_path):
    nested = tmp_path / "a" / "b" / "harness.db"
    conn = connect(nested)
    conn.close()
    assert nested.exists()


# --- tasks -------------------------------------------------------------------


def test_record_task_then_trace_open_count(store):
    store.record_task(make_task())
    assert store.trace_open_count("tr1") == 1


def test_set_task_status_done_drops_open_count(store):
    store.record_task(make_task())
    store.set_task_status("t1", "done")
    assert store.trace_open_count("tr1") == 0


@pytest.mark.parametrize("status", ["pending", "leased", "running"])
def test_open_statuses_counted(store, status):
    store.record_task(make_task(), status=status)
    assert store.trace_open_count("tr1") == 1


@pytest.mark.parametrize("status", ["done", "failed", "dead", "blocked"])
def test_closed_statuses_not_counted(store, status):
    store.record_task(make_task(), status=status)
    assert store.trace_open_count("tr1") == 0


def test_record_task_upserts_on_task_id(store):
    store.record_task(make_task(intent="first"))
    store.record_task(make_task(intent="second"), status="running")
    conn = connect(store.db_path)
    rows = [tuple(r) for r in conn.execute("SELECT intent, status FROM tasks")]
    conn.close()
    assert rows == [("second", "running")]


def test_record_task_persists_json_columns(store):
    task = make_task(
        payload={"k": [1, 2, {"nested": True}]},
        artifacts=TaskArtifacts(base_ref="abc123", inputs=["a.json", "b.json"]),
        schedule_id="s1",
        priority=2,
        attempt=3,
    )
    store.record_task(task)
    conn = connect(store.db_path)
    row = conn.execute(
        "SELECT payload_json, artifacts_json, schedule_id, priority, attempt "
        "FROM tasks WHERE task_id='t1'"
    ).fetchone()
    conn.close()
    import json

    assert json.loads(row[0]) == {"k": [1, 2, {"nested": True}]}
    assert json.loads(row[1]) == {"base_ref": "abc123", "inputs": ["a.json", "b.json"]}
    assert row[2] == "s1"
    assert (row[3], row[4]) == (2, 3)


def test_trace_open_count_isolates_traces(store):
    store.record_task(make_task("t1", trace_id="tr1"))
    store.record_task(make_task("t2", trace_id="tr2"))
    assert store.trace_open_count("tr1") == 1
    assert store.trace_open_count("tr2") == 1
    assert store.trace_open_count("tr-unknown") == 0


def test_set_task_status_unknown_task_is_noop(store):
    store.set_task_status("nope", "done")  # must not raise


# --- runs --------------------------------------------------------------------


def test_record_run_roundtrips_unchanged(store):
    store.record_task(make_task())
    run = make_run(
        exit_code=0,
        is_error=False,
        degraded=True,
        ended_at=datetime(2026, 7, 19, 12, 5, tzinfo=UTC),
        duration_ms=300_000,
        claude_session_id="sess-abc",
        num_turns=12,
        total_cost_usd=0.4213,
        workspace_path="/tmp/ws",
        output_ref="deadbeef",
        branch="run/t1",
        stdout_log="/tmp/out.log",
        stderr_log="/tmp/err.log",
    )
    store.record_run(run)
    assert store.get_run("r1") == run


def test_record_run_roundtrips_with_nulls(store):
    store.record_task(make_task())
    run = make_run(status="timeout")
    store.record_run(run)
    got = store.get_run("r1")
    assert got == run
    assert got.ended_at is None
    assert got.total_cost_usd is None


def test_get_run_missing_returns_none(store):
    assert store.get_run("nope") is None


def test_record_run_upserts_on_run_id(store):
    store.record_task(make_task())
    store.record_run(make_run(status="failed"))
    store.record_run(make_run(status="ok", num_turns=5))
    assert len(store.recent_runs()) == 1
    got = store.get_run("r1")
    assert got.status == "ok"
    assert got.num_turns == 5


def test_recent_runs_newest_first_and_respects_limit(store):
    store.record_task(make_task())
    base = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    for i in range(5):
        store.record_run(
            make_run(f"r{i}", started_at=base + timedelta(minutes=i))
        )
    ids = [r.run_id for r in store.recent_runs()]
    assert ids == ["r4", "r3", "r2", "r1", "r0"]
    assert [r.run_id for r in store.recent_runs(limit=2)] == ["r4", "r3"]


def test_trace_runs_filters_by_trace_and_orders_oldest_first(store):
    store.record_task(make_task("t1", trace_id="tr1"))
    store.record_task(make_task("t2", trace_id="tr2"))
    base = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
    store.record_run(make_run("rA", task_id="t1", trace_id="tr1", started_at=base))
    store.record_run(
        make_run(
            "rB", task_id="t1", trace_id="tr1", started_at=base + timedelta(minutes=1)
        )
    )
    store.record_run(make_run("rC", task_id="t2", trace_id="tr2", started_at=base))
    assert [r.run_id for r in store.trace_runs("tr1")] == ["rA", "rB"]
    assert [r.run_id for r in store.trace_runs("tr2")] == ["rC"]


def test_run_booleans_roundtrip_as_bools(store):
    store.record_task(make_task())
    store.record_run(make_run(is_error=True, degraded=False))
    got = store.get_run("r1")
    assert got.is_error is True
    assert got.degraded is False


# --- leaf branches -----------------------------------------------------------


def _chain(store, n: int, trace_id: str = "tr1") -> None:
    """Linear chain t1 -> t2 -> ... with one successful run each."""
    parent = None
    for i in range(1, n + 1):
        tid = f"t{i}"
        store.record_task(
            make_task(tid, trace_id=trace_id, parent_task_id=parent), status="done"
        )
        store.record_run(
            make_run(f"r{i}", task_id=tid, trace_id=trace_id, branch=f"run/{tid}")
        )
        parent = tid


def test_leaf_branches_linear_chain_returns_only_last(store):
    _chain(store, 3)
    assert store.trace_leaf_branches("tr1") == ["run/t3"]


def test_leaf_branches_fanout_returns_both_children_not_parent(store):
    store.record_task(make_task("t1", trace_id="tr1"), status="done")
    store.record_run(make_run("r1", task_id="t1", trace_id="tr1", branch="run/t1"))
    for child in ("t2", "t3"):
        store.record_task(
            make_task(child, trace_id="tr1", parent_task_id="t1"), status="done"
        )
        store.record_run(
            make_run(
                f"r-{child}", task_id=child, trace_id="tr1", branch=f"run/{child}"
            )
        )
    assert sorted(store.trace_leaf_branches("tr1")) == ["run/t2", "run/t3"]


@pytest.mark.parametrize("bad_status", ["failed", "timeout"])
def test_leaf_branches_excludes_unsuccessful_runs(store, bad_status):
    store.record_task(make_task("t1", trace_id="tr1"), status="done")
    store.record_run(
        make_run("r1", task_id="t1", trace_id="tr1", branch="run/t1", status=bad_status)
    )
    assert store.trace_leaf_branches("tr1") == []


def test_leaf_branches_ignores_runs_without_branch(store):
    store.record_task(make_task("t1", trace_id="tr1"), status="done")
    store.record_run(make_run("r1", task_id="t1", trace_id="tr1", branch=None))
    assert store.trace_leaf_branches("tr1") == []


def test_leaf_branches_isolates_traces(store):
    _chain(store, 2, trace_id="tr1")
    store.record_task(make_task("x1", trace_id="tr2"), status="done")
    store.record_run(make_run("rx", task_id="x1", trace_id="tr2", branch="run/x1"))
    assert store.trace_leaf_branches("tr1") == ["run/t2"]
    assert store.trace_leaf_branches("tr2") == ["run/x1"]


def test_leaf_branches_empty_trace(store):
    assert store.trace_leaf_branches("tr-nothing") == []


def test_leaf_branches_deduplicates_retried_runs(store):
    store.record_task(make_task("t1", trace_id="tr1"), status="done")
    store.record_run(
        make_run("r1", task_id="t1", trace_id="tr1", branch="run/t1", status="failed")
    )
    store.record_run(
        make_run(
            "r2", task_id="t1", trace_id="tr1", branch="run/t1", attempt=2, status="ok"
        )
    )
    assert store.trace_leaf_branches("tr1") == ["run/t1"]


# --- traces ------------------------------------------------------------------


def test_trace_merged_records_ref(store):
    store.trace_merged("tr1", "abc123")
    conn = connect(store.db_path)
    row = conn.execute(
        "SELECT merged_ref, merged_at FROM traces WHERE trace_id='tr1'"
    ).fetchone()
    conn.close()
    assert row[0] == "abc123"
    assert row[1] is not None


def test_trace_merged_upserts(store):
    store.trace_merged("tr1", "abc123")
    store.trace_merged("tr1", "def456")
    conn = connect(store.db_path)
    rows = [tuple(r) for r in conn.execute("SELECT merged_ref FROM traces")]
    conn.close()
    assert rows == [("def456",)]


# --- handoffs ----------------------------------------------------------------


def test_record_handoff_accepted(store):
    store.record_task(make_task("t1"))
    store.record_task(make_task("t2"))
    store.record_handoff("t1", "t2", "reviewer", True)
    conn = connect(store.db_path)
    row = conn.execute(
        "SELECT parent_task_id, child_task_id, agent, accepted, reason FROM handoffs"
    ).fetchone()
    conn.close()
    assert tuple(row) == ("t1", "t2", "reviewer", 1, None)


def test_record_handoff_rejected_with_reason(store):
    store.record_task(make_task("t1"))
    store.record_handoff("t1", None, "reviewer", False, reason="not allowed")
    conn = connect(store.db_path)
    row = conn.execute(
        "SELECT child_task_id, accepted, reason FROM handoffs"
    ).fetchone()
    conn.close()
    assert tuple(row) == (None, 0, "not allowed")


# --- events ------------------------------------------------------------------


def test_events_readable_and_ordered(store):
    store.event("queued", task_id="t1", trace_id="tr1", agent="coder")
    store.event("started", task_id="t1", trace_id="tr1", run_id="r1", agent="coder")
    store.event("finished", task_id="t1", trace_id="tr1", run_id="r1", agent="coder")
    conn = connect(store.db_path)
    rows = conn.execute("SELECT kind FROM events ORDER BY id").fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["queued", "started", "finished"]


def test_event_stores_json_data_and_nulls(store):
    store.event("thing", data={"a": 1, "b": ["x"]})
    conn = connect(store.db_path)
    row = conn.execute(
        "SELECT ts, kind, task_id, trace_id, run_id, agent, data_json FROM events"
    ).fetchone()
    conn.close()
    import json

    assert row[1] == "thing"
    assert row[2:6] == (None, None, None, None)
    assert json.loads(row[6]) == {"a": 1, "b": ["x"]}
    assert row[0] is not None


def test_event_without_data_stores_null(store):
    store.event("bare")
    conn = connect(store.db_path)
    row = conn.execute("SELECT data_json FROM events").fetchone()
    conn.close()
    assert row[0] is None


# --- schedules ---------------------------------------------------------------


def _sched(schedule_id="s1", **kw) -> ScheduleDef:
    return ScheduleDef(
        schedule_id=schedule_id,
        cron=kw.pop("cron", "0 * * * *"),
        agent=kw.pop("agent", "coder"),
        intent=kw.pop("intent", "hourly sweep"),
        **kw,
    )


def test_upsert_and_list_schedules(store):
    s = _sched(repo="app", payload={"x": 1})
    store.upsert_schedule(s, datetime(2026, 7, 19, 13, 0, tzinfo=UTC))
    assert store.list_schedules() == [s]


def test_upsert_schedule_updates_existing(store):
    store.upsert_schedule(_sched(intent="old"), datetime(2026, 7, 19, 13, 0, tzinfo=UTC))
    store.upsert_schedule(_sched(intent="new"), datetime(2026, 7, 19, 14, 0, tzinfo=UTC))
    listed = store.list_schedules()
    assert len(listed) == 1
    assert listed[0].intent == "new"
    due = store.due_schedules(datetime(2026, 7, 19, 15, 0, tzinfo=UTC))
    assert due[0][1] == datetime(2026, 7, 19, 14, 0, tzinfo=UTC)


def test_due_schedules_filters_by_time_and_enabled(store):
    now = datetime(2026, 7, 19, 13, 0, tzinfo=UTC)
    store.upsert_schedule(_sched("past"), now - timedelta(minutes=1))
    store.upsert_schedule(_sched("exactly-now"), now)
    store.upsert_schedule(_sched("future"), now + timedelta(minutes=1))
    store.upsert_schedule(_sched("disabled", enabled=False), now - timedelta(hours=1))

    due = store.due_schedules(now)
    assert sorted(s.schedule_id for s, _ in due) == ["exactly-now", "past"]
    assert all(isinstance(fire, datetime) for _, fire in due)


def test_delete_schedule(store):
    store.upsert_schedule(_sched("s1"), datetime(2026, 7, 19, 13, 0, tzinfo=UTC))
    store.upsert_schedule(_sched("s2"), datetime(2026, 7, 19, 13, 0, tzinfo=UTC))
    store.delete_schedule("s1")
    assert [s.schedule_id for s in store.list_schedules()] == ["s2"]


def test_delete_unknown_schedule_is_noop(store):
    store.delete_schedule("nope")


def test_schedule_enabled_roundtrips_as_bool(store):
    store.upsert_schedule(
        _sched("s1", enabled=False), datetime(2026, 7, 19, 13, 0, tzinfo=UTC)
    )
    assert store.list_schedules()[0].enabled is False


# --- persistence -------------------------------------------------------------


def test_store_persists_across_instances(db_path):
    RunStore(db_path).record_task(make_task())
    assert RunStore(db_path).trace_open_count("tr1") == 1


def test_datetimes_stored_as_iso_utc_strings(store):
    store.record_task(make_task())
    store.record_run(
        make_run(started_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC))
    )
    conn = connect(store.db_path)
    row = conn.execute("SELECT started_at FROM runs").fetchone()
    conn.close()
    assert isinstance(row[0], str)
    assert row[0].startswith("2026-07-19T12:00:00")


def test_naive_datetimes_are_treated_as_utc(store):
    store.record_task(make_task())
    store.record_run(make_run(started_at=datetime(2026, 7, 19, 12, 0)))
    got = store.get_run("r1")
    assert got.started_at == datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def test_foreign_keys_are_enforced_for_runs(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.record_run(make_run(task_id="does-not-exist"))
