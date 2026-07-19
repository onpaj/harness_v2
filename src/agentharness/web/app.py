"""A read-only dashboard over the queue, the store, and the limiters.

Every route is a GET. The dashboard never enqueues, retries, cancels, or edits
anything -- operator actions belong to the CLI, so a browser tab left open (or
a crawler, or a mis-click) can never perturb a running harness.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from agentharness.config import Config
from agentharness.models import RunRecord
from agentharness.obs.metrics import (
    cost_by_agent,
    cost_by_trace,
    latency_percentiles,
    snapshot,
)
from agentharness.queue.base import Queue
from agentharness.store.runs import RunStore

TEMPLATES_DIR = Path(__file__).parent / "templates"

RECENT_RUNS_ON_INDEX = 20
RUNS_PAGE_LIMIT = 200


def _run_row(run: RunRecord) -> dict[str, Any]:
    """Flatten a run for templating, with display-friendly fallbacks."""
    return {
        "run_id": run.run_id,
        "task_id": run.task_id,
        "trace_id": run.trace_id,
        "agent": run.agent,
        "attempt": run.attempt,
        "status": run.status,
        "degraded": run.degraded,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "duration_ms": run.duration_ms,
        "cost": run.total_cost_usd,
        "num_turns": run.num_turns,
        "branch": run.branch,
        "output_ref": run.output_ref,
        "workspace_path": run.workspace_path,
        "stdout_log": run.stdout_log,
        "stderr_log": run.stderr_log,
        "exit_code": run.exit_code,
        "claude_session_id": run.claude_session_id,
    }


def create_app(
    cfg: Config,
    queue: Queue,
    store: RunStore,
    limiter: Any,
    gate: Any,
) -> FastAPI:
    app = FastAPI(title="agentharness", docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    def _snapshot():
        return snapshot(cfg, queue, store, limiter, gate)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        snap = _snapshot()
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "snapshot": snap,
                "agents": sorted(snap.queue_depths),
                "cost_by_agent": cost_by_agent(store, since),
                "latency": latency_percentiles(store, since),
                "runs": [_run_row(r) for r in store.recent_runs(RECENT_RUNS_ON_INDEX)],
            },
        )

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "runs.html",
            {
                "paused": gate.paused,
                "runs": [_run_row(r) for r in store.recent_runs(RUNS_PAGE_LIMIT)],
            },
        )

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: str) -> HTMLResponse:
        record = store.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        return templates.TemplateResponse(
            request,
            "run.html",
            {
                "paused": gate.paused,
                "run": _run_row(record),
                "events": [
                    e
                    for e in store.events_for_trace(record.trace_id)
                    if e["run_id"] == run_id
                ],
            },
        )

    @app.get("/traces/{trace_id}", response_class=HTMLResponse)
    def trace_detail(request: Request, trace_id: str) -> HTMLResponse:
        records = store.trace_runs(trace_id)
        return templates.TemplateResponse(
            request,
            "trace.html",
            {
                "paused": gate.paused,
                "trace_id": trace_id,
                "runs": [_run_row(r) for r in records],
                "total_cost": cost_by_trace(store, trace_id),
                "open_tasks": store.trace_open_count(trace_id),
                "events": store.events_for_trace(trace_id),
            },
        )

    @app.get("/api/snapshot")
    def api_snapshot() -> JSONResponse:
        return JSONResponse(_snapshot().to_dict())

    return app
