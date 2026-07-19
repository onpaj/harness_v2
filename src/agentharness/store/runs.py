"""RunStore — the harness's view of tasks, runs, handoffs, schedules, and events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentharness.models import (
    RunRecord,
    ScheduleDef,
    Task,
    TaskArtifacts,
    TaskStatus,
)
from agentharness.store.db import connect

OPEN_STATUSES = ("pending", "leased", "running")


def _iso(dt: datetime | None) -> str | None:
    """Serialise a datetime as an ISO-8601 UTC string. Naive values are assumed UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        task_id=row["task_id"],
        trace_id=row["trace_id"],
        agent=row["agent"],
        attempt=row["attempt"],
        status=row["status"],
        exit_code=row["exit_code"],
        is_error=bool(row["is_error"]),
        degraded=bool(row["degraded"]),
        started_at=_dt(row["started_at"]),
        ended_at=_dt(row["ended_at"]),
        duration_ms=row["duration_ms"],
        claude_session_id=row["claude_session_id"],
        num_turns=row["num_turns"],
        total_cost_usd=row["total_cost_usd"],
        workspace_path=row["workspace_path"],
        output_ref=row["output_ref"],
        branch=row["branch"],
        stdout_log=row["stdout_log"],
        stderr_log=row["stderr_log"],
    )


def _row_to_schedule(row: sqlite3.Row) -> ScheduleDef:
    return ScheduleDef(
        schedule_id=row["schedule_id"],
        cron=row["cron"],
        agent=row["agent"],
        intent=row["intent"],
        repo=row["repo"],
        payload=json.loads(row["payload_json"]),
        enabled=bool(row["enabled"]),
    )


class RunStore:
    """Durable record of everything the harness has done.

    Each public method opens and closes its own connection, so instances are safe
    to construct per-process without holding a handle open across a long run.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        connect(self.db_path).close()  # apply migrations eagerly

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection for the duration of one operation, then close it."""
        conn = connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    # --- tasks ---------------------------------------------------------------

    def record_task(self, task: Task, status: TaskStatus = "pending") -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, trace_id, parent_task_id, agent, repo, intent,
                    payload_json, artifacts_json, idempotency_key, priority,
                    attempt, status, created_at, schedule_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET
                    trace_id=excluded.trace_id,
                    parent_task_id=excluded.parent_task_id,
                    agent=excluded.agent,
                    repo=excluded.repo,
                    intent=excluded.intent,
                    payload_json=excluded.payload_json,
                    artifacts_json=excluded.artifacts_json,
                    idempotency_key=excluded.idempotency_key,
                    priority=excluded.priority,
                    attempt=excluded.attempt,
                    status=excluded.status,
                    created_at=excluded.created_at,
                    schedule_id=excluded.schedule_id
                """,
                (
                    task.task_id,
                    task.trace_id,
                    task.parent_task_id,
                    task.agent,
                    task.repo,
                    task.intent,
                    json.dumps(task.payload),
                    json.dumps(task.artifacts.model_dump()),
                    task.idempotency_key,
                    task.priority,
                    task.attempt,
                    status,
                    _iso(task.created_at),
                    task.schedule_id,
                ),
            )

    def set_task_status(self, task_id: str, status: TaskStatus) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ? WHERE task_id = ?", (status, task_id)
            )

    def get_task_artifacts(self, task_id: str) -> TaskArtifacts | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT artifacts_json FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return TaskArtifacts(**json.loads(row["artifacts_json"]))

    # --- runs ----------------------------------------------------------------

    def record_run(self, run: RunRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, task_id, trace_id, agent, attempt, status, exit_code,
                    is_error, degraded, started_at, ended_at, duration_ms,
                    claude_session_id, num_turns, total_cost_usd, workspace_path,
                    output_ref, branch, stdout_log, stderr_log
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    task_id=excluded.task_id,
                    trace_id=excluded.trace_id,
                    agent=excluded.agent,
                    attempt=excluded.attempt,
                    status=excluded.status,
                    exit_code=excluded.exit_code,
                    is_error=excluded.is_error,
                    degraded=excluded.degraded,
                    started_at=excluded.started_at,
                    ended_at=excluded.ended_at,
                    duration_ms=excluded.duration_ms,
                    claude_session_id=excluded.claude_session_id,
                    num_turns=excluded.num_turns,
                    total_cost_usd=excluded.total_cost_usd,
                    workspace_path=excluded.workspace_path,
                    output_ref=excluded.output_ref,
                    branch=excluded.branch,
                    stdout_log=excluded.stdout_log,
                    stderr_log=excluded.stderr_log
                """,
                (
                    run.run_id,
                    run.task_id,
                    run.trace_id,
                    run.agent,
                    run.attempt,
                    run.status,
                    run.exit_code,
                    int(run.is_error),
                    int(run.degraded),
                    _iso(run.started_at),
                    _iso(run.ended_at),
                    run.duration_ms,
                    run.claude_session_id,
                    run.num_turns,
                    run.total_cost_usd,
                    run.workspace_path,
                    run.output_ref,
                    run.branch,
                    run.stdout_log,
                    run.stderr_log,
                ),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _row_to_run(row) if row else None

    def recent_runs(self, limit: int = 50) -> list[RunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    def trace_runs(self, trace_id: str) -> list[RunRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE trace_id = ? ORDER BY started_at, rowid",
                (trace_id,),
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    # --- trace-level queries -------------------------------------------------

    def trace_open_count(self, trace_id: str) -> int:
        placeholders = ",".join("?" * len(OPEN_STATUSES))
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM tasks "
                f"WHERE trace_id = ? AND status IN ({placeholders})",
                (trace_id, *OPEN_STATUSES),
            ).fetchone()
        return int(row["n"])

    def trace_leaf_branches(self, trace_id: str) -> list[str]:
        """`run/*` branches of successful runs whose task has no child task."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.branch AS branch, MIN(r.started_at) AS first_started
                FROM runs r
                JOIN tasks t ON t.task_id = r.task_id
                WHERE r.trace_id = ?
                  AND r.status = 'ok'
                  AND r.branch IS NOT NULL
                  AND t.task_id NOT IN (
                      SELECT parent_task_id FROM tasks
                      WHERE trace_id = ? AND parent_task_id IS NOT NULL
                  )
                GROUP BY r.branch
                ORDER BY first_started, r.branch
                """,
                (trace_id, trace_id),
            ).fetchall()
        return [r["branch"] for r in rows]

    def trace_merged(self, trace_id: str, merge_ref: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO traces (trace_id, merged_ref, merged_at) VALUES (?,?,?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    merged_ref=excluded.merged_ref,
                    merged_at=excluded.merged_at
                """,
                (trace_id, merge_ref, _now()),
            )

    # --- handoffs ------------------------------------------------------------

    def record_handoff(
        self,
        parent_task_id: str,
        child_task_id: str | None,
        agent: str,
        accepted: bool,
        reason: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO handoffs "
                "(parent_task_id, child_task_id, agent, accepted, reason, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (parent_task_id, child_task_id, agent, int(accepted), reason, _now()),
            )

    # --- events --------------------------------------------------------------

    def event(
        self,
        kind: str,
        *,
        task_id: str | None = None,
        trace_id: str | None = None,
        run_id: str | None = None,
        agent: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (ts, kind, task_id, trace_id, run_id, agent, data_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    _now(),
                    kind,
                    task_id,
                    trace_id,
                    run_id,
                    agent,
                    json.dumps(data) if data is not None else None,
                ),
            )

    def events_for_trace(self, trace_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, kind, task_id, trace_id, run_id, agent, data_json "
                "FROM events WHERE trace_id = ? ORDER BY id",
                (trace_id,),
            ).fetchall()
        return [self._event_row(r) for r in rows]

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, kind, task_id, trace_id, run_id, agent, data_json "
                "FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._event_row(r) for r in rows]

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        event = dict(row)
        event["data"] = json.loads(event.pop("data_json")) if event["data_json"] else None
        return event

    # --- schedules -----------------------------------------------------------

    def upsert_schedule(self, s: ScheduleDef, next_fire_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO schedules (
                    schedule_id, cron, agent, intent, repo, payload_json,
                    enabled, next_fire_at
                ) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(schedule_id) DO UPDATE SET
                    cron=excluded.cron,
                    agent=excluded.agent,
                    intent=excluded.intent,
                    repo=excluded.repo,
                    payload_json=excluded.payload_json,
                    enabled=excluded.enabled,
                    next_fire_at=excluded.next_fire_at
                """,
                (
                    s.schedule_id,
                    s.cron,
                    s.agent,
                    s.intent,
                    s.repo,
                    json.dumps(s.payload),
                    int(s.enabled),
                    _iso(next_fire_at),
                ),
            )

    def due_schedules(self, now: datetime) -> list[tuple[ScheduleDef, datetime]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM schedules "
                "WHERE enabled = 1 AND next_fire_at IS NOT NULL AND next_fire_at <= ? "
                "ORDER BY next_fire_at, schedule_id",
                (_iso(now),),
            ).fetchall()
        return [(_row_to_schedule(r), _dt(r["next_fire_at"])) for r in rows]

    def mark_fired(self, schedule_id: str, next_fire_at: datetime, fired_at: datetime) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE schedules SET next_fire_at = ?, last_fired_at = ? "
                "WHERE schedule_id = ?",
                (_iso(next_fire_at), _iso(fired_at), schedule_id),
            )

    def list_schedules(self) -> list[ScheduleDef]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM schedules ORDER BY schedule_id"
            ).fetchall()
        return [_row_to_schedule(r) for r in rows]

    def delete_schedule(self, schedule_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM schedules WHERE schedule_id = ?", (schedule_id,)
            )
