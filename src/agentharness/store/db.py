"""Schema definition and connection factory for the harness SQLite database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id        TEXT PRIMARY KEY,
    trace_id       TEXT NOT NULL,
    parent_task_id TEXT,
    agent          TEXT NOT NULL,
    repo           TEXT,
    intent         TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    artifacts_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL,
    priority       INTEGER NOT NULL DEFAULT 5,
    attempt        INTEGER NOT NULL DEFAULT 1,
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT NOT NULL,
    schedule_id    TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL REFERENCES tasks(task_id),
    trace_id          TEXT NOT NULL,
    agent             TEXT NOT NULL,
    attempt           INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL,
    exit_code         INTEGER,
    is_error          INTEGER NOT NULL DEFAULT 0,
    degraded          INTEGER NOT NULL DEFAULT 0,
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    duration_ms       INTEGER,
    claude_session_id TEXT,
    num_turns         INTEGER,
    total_cost_usd    REAL,
    workspace_path    TEXT,
    output_ref        TEXT,
    branch            TEXT,
    stdout_log        TEXT,
    stderr_log        TEXT
);

CREATE TABLE IF NOT EXISTS handoffs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_task_id TEXT NOT NULL,
    child_task_id  TEXT,
    agent          TEXT NOT NULL,
    accepted       INTEGER NOT NULL,
    reason         TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    schedule_id   TEXT PRIMARY KEY,
    cron          TEXT NOT NULL,
    agent         TEXT NOT NULL,
    intent        TEXT NOT NULL,
    repo          TEXT,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    enabled       INTEGER NOT NULL DEFAULT 1,
    next_fire_at  TEXT,
    last_fired_at TEXT
);

CREATE TABLE IF NOT EXISTS traces (
    trace_id   TEXT PRIMARY KEY,
    merged_ref TEXT,
    merged_at  TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    task_id   TEXT,
    trace_id  TEXT,
    run_id    TEXT,
    agent     TEXT,
    data_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_trace  ON tasks(trace_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_runs_trace   ON runs(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_ts    ON events(ts);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the harness database, enabling WAL + foreign keys and applying migrations."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")

    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    # BEGIN/COMMIT live inside the script: `executescript` implicitly commits any
    # transaction opened beforehand, so wrapping it from the outside would not work.
    conn.executescript(f"BEGIN;\n{SCHEMA}\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;")
