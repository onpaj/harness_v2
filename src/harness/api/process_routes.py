"""The process editor. Sees only the `ProcessAdmin` / `RepositoryRegistry`
ports, never a driver — the same rule `routes.py` follows for `BoardView`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from harness.models import RepositoryScope
from harness.ports.processes import ProcessAdmin, ProcessValidationError
from harness.ports.repos import RepositoryRegistry
from harness.ports.workflows import WorkflowNotFound

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _stale(repositories: RepositoryScope, known: list[str]) -> tuple[str, ...]:
    """Stored repo names no longer present in the registry — shown as a
    warning, never silently dropped."""
    if repositories == "*":
        return ()
    return tuple(repo for repo in repositories if repo not in known)


def build_process_router(admin: ProcessAdmin, registry: RepositoryRegistry) -> APIRouter:
    router = APIRouter()

    def _edit_context(
        request: Request, name: str, *, scope: str, selected: tuple[str, ...],
        error: str | None = None,
    ) -> dict:
        return {
            "request": request,
            "name": name,
            "known_repositories": sorted(registry.names()),
            "scope": scope,
            "selected": selected,
            "error": error,
        }

    @router.get("/processes", response_class=HTMLResponse)
    def list_processes(request: Request) -> HTMLResponse:
        known = registry.names()
        rows = []
        for name in admin.list_processes():
            summary = admin.load_process(name)
            rows.append(
                {
                    "name": summary.name,
                    "repositories": summary.repositories,
                    "stale": _stale(summary.repositories, known),
                }
            )
        return TEMPLATES.TemplateResponse(
            request=request, name="processes_list.html", context={"processes": rows}
        )

    @router.get("/processes/{name}/edit", response_class=HTMLResponse)
    def edit_process(request: Request, name: str) -> HTMLResponse:
        try:
            summary = admin.load_process(name)
        except WorkflowNotFound:
            raise HTTPException(
                status_code=404, detail=f"process {name} does not exist"
            ) from None
        scope = "all" if summary.repositories == "*" else "specific"
        selected = () if summary.repositories == "*" else summary.repositories
        context = _edit_context(request, name, scope=scope, selected=selected)
        context["start"] = summary.start
        context["steps"] = summary.steps
        return TEMPLATES.TemplateResponse(
            request=request, name="process_edit.html", context=context
        )

    @router.post("/processes/{name}/edit", response_class=HTMLResponse)
    def save_process(
        request: Request,
        name: str,
        scope: str = Form(...),
        repositories: list[str] = Form(default_factory=list),
    ) -> HTMLResponse:
        selected: RepositoryScope = "*" if scope == "all" else tuple(repositories)
        try:
            admin.save_repositories(name, selected)
        except ProcessValidationError as error:
            context = _edit_context(
                request, name, scope=scope, selected=tuple(repositories),
                error=str(error),
            )
            try:
                summary = admin.load_process(name)
                context["start"] = summary.start
                context["steps"] = summary.steps
            except WorkflowNotFound:
                context["start"] = ""
                context["steps"] = ()
            return TEMPLATES.TemplateResponse(
                request=request, name="process_edit.html", status_code=422,
                context=context,
            )
        except WorkflowNotFound:
            raise HTTPException(
                status_code=404, detail=f"process {name} does not exist"
            ) from None
        return RedirectResponse(url="/processes", status_code=303)

    return router
