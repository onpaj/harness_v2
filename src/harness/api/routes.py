"""Endpoints. Sees only the BoardView / ArtifactView / StageOutputView ports."""

from __future__ import annotations

import html
import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates

from harness.models import END
from harness.ports.agent import AgentNotFound, AgentSpec
from harness.ports.agent_admin import AgentAdmin, AgentFields, AgentValidationError
from harness.ports.artifacts import ArtifactView
from harness.ports.board import DONE_COLUMN, FAILED_COLUMN, TODO_COLUMN, BoardView
from harness.ports.clock import Clock
from harness.ports.control import TaskControl
from harness.ports.logs import StageOutputView
from harness.ports.workflow_admin import WorkflowAdmin, WorkflowValidationError
from harness.ports.workflows import WorkflowNotFound

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _basename(value: str | None) -> str:
    """The last path segment. A name without a slash passes through unchanged.

    `repository` is meant to be a name (invariant 15), `worktree` is the path
    `<root>/<task_id>` — both are shown in the UI as a bare name, not a path.
    """
    if not value:
        return ""
    return value.rstrip("/").rsplit("/", 1)[-1]


def _shorttime(value: str | None) -> str:
    """A compact, human-readable rendering of an ISO-8601 timestamp for the UI
    (e.g. "Jul 19, 10:00"). The templates keep the full value in a `title`/
    `datetime` attribute, so the exact timestamp is never lost — this only
    shortens what the eye reads. An unparseable value passes through unchanged."""
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return value
    return parsed.strftime("%b %d, %H:%M")


TEMPLATES.env.filters["basename"] = _basename
TEMPLATES.env.filters["shorttime"] = _shorttime


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


async def stage_output_stream(
    output: StageOutputView, task_id: str
) -> AsyncIterator[str]:
    """SSE frames carrying the actual output lines (unlike the board's
    revision-only frames): one `line` event per rendered line, then a single
    `end` event when the stage finishes so the client closes the EventSource.

    Each line is HTML-escaped — the agent's output is untrusted and the client
    appends it as innerHTML.
    """
    async for line in output.subscribe(task_id):
        # A newline inside `data:` would truncate the SSE frame, so collapse any
        # stray one (rendered lines shouldn't carry them, but this is untrusted
        # output). Wrapped in a <div> so htmx breaks each line cleanly; the line
        # is escaped first, so it cannot break out of the wrapper.
        safe = html.escape(line.replace("\r", "").replace("\n", " "))
        yield f"event: line\ndata: <div>{safe}</div>\n\n"
    yield "event: end\ndata: \n\n"


def _agent_spec_dict(spec: AgentSpec) -> dict:
    return {
        "name": spec.name,
        "prompt": spec.prompt,
        "model": spec.model,
        "fallback_model": spec.fallback_model,
        "allowed_tools": list(spec.allowed_tools),
        "allowed_outcomes": [outcome.value for outcome in spec.allowed_outcomes],
    }


def _agent_fields_from(payload: dict) -> AgentFields:
    return AgentFields(
        prompt=payload.get("prompt") or "",
        model=payload.get("model") or None,
        fallback_model=payload.get("fallback_model") or None,
        allowed_tools=tuple(payload.get("allowed_tools") or ()),
        allowed_outcomes=tuple(payload.get("allowed_outcomes") or ("done",)),
    )


def _new_step_warnings(view: BoardView, raw_json: str) -> list[str]:
    """Steps a just-written, schema-valid workflow references that the
    *running* harness has no queue for (invariant #22: board columns other
    than todo/done/failed mirror `step_queues` exactly, by construction —
    see architecture-01.md). Computed here, not inside WorkflowAdmin, because
    a filesystem driver has no idea what the running process's queues are."""
    known_steps = {
        column.name
        for tab in view.snapshot().workflows
        for column in tab.columns
    } - {TODO_COLUMN, DONE_COLUMN, FAILED_COLUMN}
    parsed = json.loads(raw_json)
    referenced = {parsed.get("start")}
    for item in parsed.get("transitions", []):
        referenced.add(item.get("from"))
        referenced.add(item.get("to"))
    referenced.discard(None)
    referenced.discard(END)
    new_steps = sorted(referenced - known_steps)
    return [
        f"step {step!r} is new — the running harness won't route to it until "
        "it is restarted"
        for step in new_steps
    ]


def _agent_form_context(
    *,
    name: str,
    is_new: bool,
    prompt: str,
    model: str | None,
    fallback_model: str | None,
    allowed_tools: tuple[str, ...],
    allowed_outcomes: tuple[str, ...],
    errors: dict[str, str],
    saved: bool,
) -> dict:
    return {
        "name": name,
        "is_new": is_new,
        "prompt": prompt,
        "model": model or "",
        "fallback_model": fallback_model or "",
        "allowed_tools": ", ".join(allowed_tools),
        "checked_outcomes": set(allowed_outcomes),
        "errors": errors,
        "saved": saved,
    }


def _agent_fields_from_form(form) -> AgentFields:
    tools_raw = (form.get("allowed_tools") or "").strip()
    allowed_tools = tuple(item.strip() for item in tools_raw.split(",") if item.strip())
    return AgentFields(
        prompt=form.get("prompt") or "",
        model=(form.get("model") or "").strip() or None,
        fallback_model=(form.get("fallback_model") or "").strip() or None,
        allowed_tools=allowed_tools,
        allowed_outcomes=tuple(form.getlist("allowed_outcomes")),
    )


def _workflow_editor_context(
    *,
    name: str,
    is_new: bool,
    text: str,
    error: str | None,
    warnings: list[str],
    saved: bool,
) -> dict:
    return {
        "name": name,
        "is_new": is_new,
        "text": text,
        "error": error,
        "warnings": warnings,
        "saved": saved,
    }


def build_json_router(
    view: BoardView,
    artifacts: ArtifactView,
    agent_admin: AgentAdmin,
    workflow_admin: WorkflowAdmin,
    version: str,
    build_time: str | None,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/board")
    def board() -> dict:
        return view.snapshot().to_dict()

    @router.get("/version")
    def version_info() -> dict:
        return {"version": version, "build_time": build_time}

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

    @router.get("/agents")
    def list_agents() -> dict:
        return {"agents": list(agent_admin.list())}

    @router.get("/agents/{name}")
    def get_agent(name: str) -> dict:
        try:
            spec = agent_admin.read(name)
        except AgentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        return _agent_spec_dict(spec)

    @router.put("/agents/{name}")
    async def put_agent(name: str, request: Request) -> JSONResponse:
        payload = await request.json()
        fields = _agent_fields_from(payload)
        try:
            spec = agent_admin.write(name, fields)
        except AgentValidationError as error:
            return JSONResponse(status_code=422, content={"errors": error.errors})
        return JSONResponse(content=_agent_spec_dict(spec))

    @router.delete("/agents/{name}", status_code=204)
    def delete_agent(name: str) -> Response:
        if not agent_admin.delete(name):
            raise HTTPException(status_code=404, detail=f"agent {name!r} does not exist")
        return Response(status_code=204)

    @router.get("/workflows")
    def list_workflows() -> dict:
        return {"workflows": list(workflow_admin.list())}

    @router.get("/workflows/{name}")
    def get_workflow(name: str) -> PlainTextResponse:
        try:
            raw = workflow_admin.read_raw(name)
        except WorkflowNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        return PlainTextResponse(raw)

    @router.put("/workflows/{name}")
    async def put_workflow(name: str, request: Request) -> JSONResponse:
        raw_json = (await request.body()).decode("utf-8")
        try:
            workflow_admin.write_raw(name, raw_json)
        except WorkflowValidationError as error:
            return JSONResponse(status_code=422, content={"error": str(error)})
        return JSONResponse(content={"warnings": _new_step_warnings(view, raw_json)})

    @router.delete("/workflows/{name}", status_code=204)
    def delete_workflow(name: str) -> Response:
        if not workflow_admin.delete(name):
            raise HTTPException(
                status_code=404, detail=f"workflow {name!r} does not exist"
            )
        return Response(status_code=204)

    return router


def build_html_router(
    view: BoardView,
    artifacts: ArtifactView,
    output: StageOutputView,
    control: TaskControl,
    clock: Clock,
    coalesce_seconds: float,
    agent_admin: AgentAdmin,
    workflow_admin: WorkflowAdmin,
    version: str,
    build_time: str | None,
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
            request=request,
            name="board.html",
            context={
                "board": view.snapshot(),
                "version": version,
                "build_time": build_time,
            },
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

    @router.get("/api/tasks/{task_id}/output/events")
    async def task_output(task_id: str) -> StreamingResponse:
        return StreamingResponse(
            stage_output_stream(output, task_id),
            media_type="text/event-stream",
        )

    # --- Agents admin -------------------------------------------------------

    @router.get("/admin/agents", response_class=HTMLResponse)
    def list_agents_page(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/agents_list.html",
            context={"names": agent_admin.list()},
        )

    # Registered before /admin/agents/{name}: FastAPI matches routes in
    # registration order, and a dynamic path would otherwise swallow "new".
    @router.get("/admin/agents/new", response_class=HTMLResponse)
    def new_agent_page(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/agent_form.html",
            context=_agent_form_context(
                name="",
                is_new=True,
                prompt="",
                model=None,
                fallback_model=None,
                allowed_tools=(),
                allowed_outcomes=("done",),
                errors={},
                saved=False,
            ),
        )

    @router.get("/admin/agents/{name}", response_class=HTMLResponse)
    def edit_agent_page(request: Request, name: str) -> HTMLResponse:
        try:
            spec = agent_admin.read(name)
        except AgentNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/agent_form.html",
            context=_agent_form_context(
                name=spec.name,
                is_new=False,
                prompt=spec.prompt,
                model=spec.model,
                fallback_model=spec.fallback_model,
                allowed_tools=spec.allowed_tools,
                allowed_outcomes=tuple(outcome.value for outcome in spec.allowed_outcomes),
                errors={},
                saved=False,
            ),
        )

    @router.post("/admin/agents", response_class=HTMLResponse)
    async def create_agent_page(request: Request) -> HTMLResponse:
        form = await request.form()
        name = (form.get("name") or "").strip()
        fields = _agent_fields_from_form(form)
        errors: dict[str, str] = {}
        if not name:
            errors["name"] = "name is required"
        elif name in agent_admin.list():
            errors["name"] = f"an agent named {name!r} already exists"
        if not errors:
            try:
                spec = agent_admin.write(name, fields)
            except AgentValidationError as error:
                errors = error.errors
        if errors:
            return TEMPLATES.TemplateResponse(
                request=request,
                name="admin/agent_form.html",
                context=_agent_form_context(
                    name=name,
                    is_new=True,
                    prompt=fields.prompt,
                    model=fields.model,
                    fallback_model=fields.fallback_model,
                    allowed_tools=fields.allowed_tools,
                    allowed_outcomes=fields.allowed_outcomes,
                    errors=errors,
                    saved=False,
                ),
            )
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/agent_form.html",
            context=_agent_form_context(
                name=spec.name,
                is_new=False,
                prompt=spec.prompt,
                model=spec.model,
                fallback_model=spec.fallback_model,
                allowed_tools=spec.allowed_tools,
                allowed_outcomes=tuple(outcome.value for outcome in spec.allowed_outcomes),
                errors={},
                saved=True,
            ),
        )

    @router.post("/admin/agents/{name}", response_class=HTMLResponse)
    async def update_agent_page(request: Request, name: str) -> HTMLResponse:
        form = await request.form()
        fields = _agent_fields_from_form(form)
        try:
            spec = agent_admin.write(name, fields)
        except AgentValidationError as error:
            return TEMPLATES.TemplateResponse(
                request=request,
                name="admin/agent_form.html",
                context=_agent_form_context(
                    name=name,
                    is_new=False,
                    prompt=fields.prompt,
                    model=fields.model,
                    fallback_model=fields.fallback_model,
                    allowed_tools=fields.allowed_tools,
                    allowed_outcomes=fields.allowed_outcomes,
                    errors=error.errors,
                    saved=False,
                ),
            )
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/agent_form.html",
            context=_agent_form_context(
                name=spec.name,
                is_new=False,
                prompt=spec.prompt,
                model=spec.model,
                fallback_model=spec.fallback_model,
                allowed_tools=spec.allowed_tools,
                allowed_outcomes=tuple(outcome.value for outcome in spec.allowed_outcomes),
                errors={},
                saved=True,
            ),
        )

    @router.post("/admin/agents/{name}/delete")
    def delete_agent_page(name: str) -> RedirectResponse:
        agent_admin.delete(name)
        return RedirectResponse(url="/admin/agents", status_code=303)

    # --- Workflows admin -----------------------------------------------------

    @router.get("/admin/workflows", response_class=HTMLResponse)
    def list_workflows_page(request: Request) -> HTMLResponse:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/workflows_list.html",
            context={"names": workflow_admin.list()},
        )

    # Registered before /admin/workflows/{name} — same reason as agents/new.
    @router.get("/admin/workflows/new", response_class=HTMLResponse)
    def new_workflow_page(request: Request) -> HTMLResponse:
        skeleton = json.dumps({"start": "", "transitions": []}, indent=2)
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/workflow_editor.html",
            context=_workflow_editor_context(
                name="", is_new=True, text=skeleton, error=None, warnings=[], saved=False
            ),
        )

    @router.get("/admin/workflows/{name}", response_class=HTMLResponse)
    def edit_workflow_page(request: Request, name: str) -> HTMLResponse:
        try:
            text = workflow_admin.read_raw(name)
        except WorkflowNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/workflow_editor.html",
            context=_workflow_editor_context(
                name=name, is_new=False, text=text, error=None, warnings=[], saved=False
            ),
        )

    @router.post("/admin/workflows", response_class=HTMLResponse)
    async def create_workflow_page(request: Request) -> HTMLResponse:
        form = await request.form()
        name = (form.get("name") or "").strip()
        text = form.get("text") or ""
        error: str | None = None
        if not name:
            error = "name is required"
        elif name in workflow_admin.list():
            error = f"a workflow named {name!r} already exists"
        if error is None:
            try:
                workflow_admin.write_raw(name, text)
            except WorkflowValidationError as write_error:
                error = str(write_error)
        if error is not None:
            return TEMPLATES.TemplateResponse(
                request=request,
                name="admin/workflow_editor.html",
                context=_workflow_editor_context(
                    name=name, is_new=True, text=text, error=error, warnings=[], saved=False
                ),
            )
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/workflow_editor.html",
            context=_workflow_editor_context(
                name=name,
                is_new=False,
                text=text,
                error=None,
                warnings=_new_step_warnings(view, text),
                saved=True,
            ),
        )

    @router.post("/admin/workflows/{name}", response_class=HTMLResponse)
    async def update_workflow_page(request: Request, name: str) -> HTMLResponse:
        form = await request.form()
        text = form.get("text") or ""
        try:
            workflow_admin.write_raw(name, text)
        except WorkflowValidationError as error:
            return TEMPLATES.TemplateResponse(
                request=request,
                name="admin/workflow_editor.html",
                context=_workflow_editor_context(
                    name=name,
                    is_new=False,
                    text=text,
                    error=str(error),
                    warnings=[],
                    saved=False,
                ),
            )
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/workflow_editor.html",
            context=_workflow_editor_context(
                name=name,
                is_new=False,
                text=text,
                error=None,
                warnings=_new_step_warnings(view, text),
                saved=True,
            ),
        )

    @router.post("/admin/workflows/{name}/delete")
    def delete_workflow_page(name: str) -> RedirectResponse:
        workflow_admin.delete(name)
        return RedirectResponse(url="/admin/workflows", status_code=303)

    return router
