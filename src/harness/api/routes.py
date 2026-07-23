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
from harness.ports.board import (
    DONE_COLUMN,
    FAILED_COLUMN,
    TODO_COLUMN,
    AgentActivity,
    BoardView,
)
from harness.ports.clock import Clock
from harness.ports.control import TaskControl
from harness.ports.logs import StageOutputView
from harness.ports.process_admin import (
    ProcessAdmin,
    ProcessAdminValidationError,
    ProcessFields,
    ProcessNotFound,
)
from harness.ports.updater import Updater, UpdateError
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


TEMPLATES.env.filters["basename"] = _basename


def _shorttime(value: str | None) -> str:
    """Compact display form of a stored ISO-8601 UTC timestamp, e.g.
    "Jul 22, 09:03". Defensive like `_basename`: anything it can't parse
    (None, already-short text, garbage) passes through unchanged — the
    template always keeps the raw value available too (e.g. in a `title`
    attribute), so a parse failure here never hides data.
    """
    if not value:
        return value or ""
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return value
    return parsed.strftime("%b %d, %H:%M")


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
    history: tuple[AgentActivity, ...] = (),
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
        "history": history,
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


def _target_option_groups(view: BoardView) -> tuple[list[str], list[str]]:
    """The workflow and step names the *running* board knows — the target
    picker's two suggestion groups (the form filters by the chosen target
    kind). Reuses the same `view.snapshot()` source `_new_step_warnings` reads
    (invariant #22): the step columns (minus todo/done/failed) plus the workflow
    tab names. `api/` never imports a driver — options flow through BoardView."""
    snapshot = view.snapshot()
    steps = {
        column.name for tab in snapshot.workflows for column in tab.columns
    } - {TODO_COLUMN, DONE_COLUMN, FAILED_COLUMN}
    workflows = {tab.name for tab in snapshot.workflows}
    return sorted(workflows), sorted(steps)


def _process_fields_dict(name: str, fields: ProcessFields) -> dict:
    return {
        "name": name,
        "cadence": fields.cadence,
        "interval": fields.interval,
        "cron": fields.cron,
        "check": fields.check,
        "target_kind": fields.target_kind,
        "target": fields.target,
        "params": dict(fields.params),
        "sink_kind": fields.sink_kind,
        "dedup": fields.dedup,
    }


def _process_fields_from(payload: dict) -> ProcessFields:
    cadence = (payload.get("cadence") or "interval").strip()
    return ProcessFields(
        cadence=cadence,
        interval=(payload.get("interval") or "").strip(),
        cron=(payload.get("cron") or "").strip(),
        check=(payload.get("check") or "").strip(),
        target_kind=(payload.get("target_kind") or "workflow").strip(),
        target=(payload.get("target") or "").strip(),
        params=payload.get("params") or {},
        sink_kind=(payload.get("sink_kind") or "none").strip(),
        dedup=(payload.get("dedup") or "per-interval").strip(),
    )


def _process_fields_from_form(form) -> ProcessFields:
    """Build `ProcessFields` from the submitted form, parsing the `params` JSON
    textarea (empty → `{}`). A syntactically invalid or non-object params box is
    a `ProcessAdminValidationError({"params": ...})`, surfaced on the form the
    same way a compile failure is."""
    params_text = (form.get("params") or "").strip()
    if not params_text:
        params: dict = {}
    else:
        try:
            params = json.loads(params_text)
        except json.JSONDecodeError as error:
            raise ProcessAdminValidationError(
                {"params": f"params must be valid JSON: {error}"}
            ) from None
        if not isinstance(params, dict):
            raise ProcessAdminValidationError(
                {"params": "params must be a JSON object"}
            )
    cadence = (form.get("cadence") or "interval").strip()
    return ProcessFields(
        cadence=cadence,
        interval=(form.get("interval") or "").strip(),
        cron=(form.get("cron") or "").strip(),
        check=(form.get("check") or "").strip(),
        target_kind=(form.get("target_kind") or "workflow").strip(),
        target=(form.get("target") or "").strip(),
        params=params,
        sink_kind=(form.get("sink_kind") or "none").strip(),
        dedup=(form.get("dedup") or "per-interval").strip(),
    )


def _process_form_context(
    *,
    name: str,
    is_new: bool,
    fields: ProcessFields,
    params_text: str | None,
    check_names: tuple[str, ...],
    sink_kinds: tuple[str, ...],
    workflow_options: list[str],
    step_options: list[str],
    errors: dict[str, str],
    saved: bool,
) -> dict:
    if params_text is None:
        params_text = json.dumps(fields.params, indent=2) if fields.params else ""
    return {
        "name": name,
        "is_new": is_new,
        "cadence": fields.cadence,
        "interval": fields.interval,
        "cron": fields.cron,
        "check": fields.check,
        "target_kind": fields.target_kind,
        "target": fields.target,
        "params": params_text,
        "sink_kind": fields.sink_kind,
        "dedup": fields.dedup,
        "check_names": list(check_names),
        "sink_kinds": list(sink_kinds),
        "workflow_options": workflow_options,
        "step_options": step_options,
        "dedup_options": ["per-interval", "per-state"],
        "errors": errors,
        "saved": saved,
    }


_NEW_PROCESS_FIELDS = ProcessFields(
    cadence="interval", interval="", cron="", check="always", target_kind="workflow", target=""
)


def build_json_router(
    view: BoardView,
    artifacts: ArtifactView,
    agent_admin: AgentAdmin,
    workflow_admin: WorkflowAdmin,
    process_admin: ProcessAdmin,
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

    @router.get("/agents/{name}/history")
    def get_agent_history(name: str) -> dict:
        # No existence check: history is about tasks the agent ran, independent of
        # whether its persona file still exists — a deleted agent can still have a
        # trail worth reading. An unknown name simply yields an empty list.
        return {"history": [activity.to_dict() for activity in view.agent_history(name)]}

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

    @router.get("/processes")
    def list_processes() -> dict:
        return {"processes": list(process_admin.list())}

    @router.get("/processes/{name}")
    def get_process(name: str) -> dict:
        try:
            fields = process_admin.read(name)
        except ProcessNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        return _process_fields_dict(name, fields)

    @router.put("/processes/{name}")
    async def put_process(name: str, request: Request) -> JSONResponse:
        payload = await request.json()
        fields = _process_fields_from(payload)
        try:
            written = process_admin.write(name, fields)
        except ProcessAdminValidationError as error:
            return JSONResponse(status_code=422, content={"errors": error.errors})
        return JSONResponse(content=_process_fields_dict(name, written))

    @router.delete("/processes/{name}", status_code=204)
    def delete_process(name: str) -> Response:
        if not process_admin.delete(name):
            raise HTTPException(
                status_code=404, detail=f"process {name!r} does not exist"
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
    process_admin: ProcessAdmin,
    updater: Updater,
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

    @router.post("/tasks/{task_id}/delete", response_class=HTMLResponse)
    def delete_task(task_id: str) -> HTMLResponse:
        if not control.delete(task_id):
            raise HTTPException(status_code=404, detail=f"task {task_id} does not exist")
        # No fragment left to render — the task is gone. The empty body is the
        # "gone" signal board.html's afterSwap listener keys off to close #detail
        # instead of reopening it. The board redraws via the SSE revision bump.
        return HTMLResponse("")

    # Sync `def`, so FastAPI runs it in a threadpool: the blocking `uv` upgrade
    # never stalls the event loop the harness shares. A successful restart may
    # kill this process before the response flushes (invariant: the button asked
    # for exactly that) — the driver reports what it did so the common
    # not-under-launchd path still returns a message.
    @router.post("/admin/update", response_class=HTMLResponse)
    def trigger_update(request: Request) -> HTMLResponse:
        try:
            result = updater.update()
        except UpdateError as error:
            return TEMPLATES.TemplateResponse(
                request=request,
                name="admin/_update_result.html",
                context={"kind": "error", "message": str(error)},
            )
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/_update_result.html",
            context={
                "kind": "ok" if result.changed else "muted",
                "message": result.detail,
            },
        )

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
                history=view.agent_history(spec.name),
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
                    history=view.agent_history(name),
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
                history=view.agent_history(spec.name),
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

    # --- Processes admin -----------------------------------------------------

    def _process_form_response(
        request: Request,
        *,
        name: str,
        is_new: bool,
        fields: ProcessFields,
        params_text: str | None,
        errors: dict[str, str],
        saved: bool,
    ) -> HTMLResponse:
        workflow_options, step_options = _target_option_groups(view)
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/process_form.html",
            context=_process_form_context(
                name=name,
                is_new=is_new,
                fields=fields,
                params_text=params_text,
                check_names=process_admin.check_names(),
                sink_kinds=process_admin.sink_kinds(),
                workflow_options=workflow_options,
                step_options=step_options,
                errors=errors,
                saved=saved,
            ),
        )

    @router.get("/admin/processes", response_class=HTMLResponse)
    def list_processes_page(request: Request) -> HTMLResponse:
        # The list page shows a summary card per process, so it reads each
        # definition — a file that fails to read (hand-edited into a broken
        # shape) still gets a card, marked unreadable, instead of vanishing.
        entries = []
        for name in process_admin.list():
            try:
                fields = process_admin.read(name)
            except ProcessNotFound:
                fields = None
            entries.append({"name": name, "fields": fields})
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin/processes_list.html",
            context={"processes": entries},
        )

    # Registered before /admin/processes/{name} — same reason as agents/new.
    @router.get("/admin/processes/new", response_class=HTMLResponse)
    def new_process_page(request: Request) -> HTMLResponse:
        return _process_form_response(
            request,
            name="",
            is_new=True,
            fields=_NEW_PROCESS_FIELDS,
            params_text="",
            errors={},
            saved=False,
        )

    @router.get("/admin/processes/{name}", response_class=HTMLResponse)
    def edit_process_page(request: Request, name: str) -> HTMLResponse:
        try:
            fields = process_admin.read(name)
        except ProcessNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        return _process_form_response(
            request,
            name=name,
            is_new=False,
            fields=fields,
            params_text=None,
            errors={},
            saved=False,
        )

    @router.post("/admin/processes", response_class=HTMLResponse)
    async def create_process_page(request: Request) -> HTMLResponse:
        form = await request.form()
        name = (form.get("name") or "").strip()
        errors: dict[str, str] = {}
        try:
            fields = _process_fields_from_form(form)
        except ProcessAdminValidationError as error:
            # An unparseable params box — keep the raw text the operator typed.
            fields = _process_fields_from(
                {key: form.get(key) for key in form.keys()}
            )
            if not name:
                errors["name"] = "name is required"
            errors.update(error.errors)
            return _process_form_response(
                request,
                name=name,
                is_new=True,
                fields=fields,
                params_text=(form.get("params") or ""),
                errors=errors,
                saved=False,
            )
        if not name:
            errors["name"] = "name is required"
        elif name in process_admin.list():
            errors["name"] = f"a process named {name!r} already exists"
        if not errors:
            try:
                written = process_admin.write(name, fields)
            except ProcessAdminValidationError as error:
                errors = error.errors
        if errors:
            return _process_form_response(
                request,
                name=name,
                is_new=True,
                fields=fields,
                params_text=(form.get("params") or ""),
                errors=errors,
                saved=False,
            )
        return _process_form_response(
            request,
            name=name,
            is_new=False,
            fields=written,
            params_text=None,
            errors={},
            saved=True,
        )

    @router.post("/admin/processes/{name}", response_class=HTMLResponse)
    async def update_process_page(request: Request, name: str) -> HTMLResponse:
        form = await request.form()
        try:
            fields = _process_fields_from_form(form)
            written = process_admin.write(name, fields)
        except ProcessAdminValidationError as error:
            fields = _process_fields_from(
                {key: form.get(key) for key in form.keys()}
            )
            return _process_form_response(
                request,
                name=name,
                is_new=False,
                fields=fields,
                params_text=(form.get("params") or ""),
                errors=error.errors,
                saved=False,
            )
        return _process_form_response(
            request,
            name=name,
            is_new=False,
            fields=written,
            params_text=None,
            errors={},
            saved=True,
        )

    @router.post("/admin/processes/{name}/delete")
    def delete_process_page(name: str) -> RedirectResponse:
        process_admin.delete(name)
        return RedirectResponse(url="/admin/processes", status_code=303)

    return router
