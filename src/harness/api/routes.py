"""Endpoints. Sees only the BoardView port."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from harness.ports.artifacts import ArtifactView
from harness.ports.board import BoardView
from harness.ports.clock import Clock
from harness.ports.control import TaskControl

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _basename(value: str | None) -> str:
    """Poslední segment cesty. Jméno bez lomítka projde beze změny.

    `repository` má být jméno (invariant 15), `worktree` je cesta
    `<root>/<task_id>` — obojí se v UI ukazuje jako holé jméno, ne cesta.
    """
    if not value:
        return ""
    return value.rstrip("/").rsplit("/", 1)[-1]


TEMPLATES.env.filters["basename"] = _basename


async def board_event_stream(
    view: BoardView, clock: Clock, coalesce_seconds: float
) -> AsyncIterator[str]:
    """SSE frames. They carry only the revision number — the client fetches
    the data itself.

    After each frame it waits coalesce_seconds; changes that happen in the
    meantime are merged into a single notification. Without this, five
    consumers could hammer the board with redraws.
    """
    async for revision in view.subscribe():
        yield f"event: board\ndata: {json.dumps({'revision': revision})}\n\n"
        await clock.sleep(coalesce_seconds)


def build_json_router(view: BoardView, artifacts: ArtifactView) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/board")
    def board() -> dict:
        return view.snapshot().to_dict()

    @router.get("/tasks/{task_id}")
    def task(task_id: str) -> dict:
        found = view.get(task_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"task {task_id} does not exist")
        return found.to_dict()

    @router.get("/tasks/{task_id}/artifacts")
    def task_artifacts(task_id: str) -> dict:
        refs = artifacts.list(task_id)
        return {"artifacts": [ref.to_dict() for ref in refs]}

    @router.get("/tasks/{task_id}/artifacts/{step}/{attempt}/{name}")
    def task_artifact(
        task_id: str, step: str, attempt: int, name: str
    ) -> PlainTextResponse:
        content = artifacts.read(task_id, step, attempt, name)
        if content is None:
            raise HTTPException(
                status_code=404, detail=f"artifact {name} does not exist"
            )
        return PlainTextResponse(content)

    return router


def build_html_router(
    view: BoardView,
    artifacts: ArtifactView,
    control: TaskControl,
    clock: Clock,
    coalesce_seconds: float,
) -> APIRouter:
    router = APIRouter()

    def _task_fragment(request: Request, task_id: str) -> HTMLResponse:
        found = view.get(task_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"task {task_id} does not exist")
        return TEMPLATES.TemplateResponse(
            request=request,
            name="_task.html",
            context={"task": found, "artifacts": artifacts.list(task_id)},
        )

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request, name="board.html", context={"board": view.snapshot()}
        )

    @router.get("/fragment/board", response_class=HTMLResponse)
    def fragment_board(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request, name="_columns.html", context={"board": view.snapshot()}
        )

    @router.get("/fragment/task/{task_id}", response_class=HTMLResponse)
    def fragment_task(request: Request, task_id: str) -> HTMLResponse:
        return _task_fragment(request, task_id)

    @router.post("/tasks/{task_id}/restart", response_class=HTMLResponse)
    def restart_task(request: Request, task_id: str) -> HTMLResponse:
        if not control.restart(task_id):
            raise HTTPException(
                status_code=404, detail=f"task {task_id} is not a failed task"
            )
        # The projection updated synchronously via the emitted event, so the
        # refreshed fragment shows the task now in `todo`. The board redraws via SSE.
        return _task_fragment(request, task_id)

    @router.get("/api/events")
    async def events() -> StreamingResponse:
        return StreamingResponse(
            board_event_stream(view, clock, coalesce_seconds),
            media_type="text/event-stream",
        )

    return router
