"""API factory. BoardView comes from outside — so it knows nothing about drivers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from harness.api.routes import build_html_router, build_json_router
from harness.ports.agent import AgentNotFound, AgentSpec
from harness.ports.agent_admin import AgentAdmin, AgentFields, AgentValidationError
from harness.ports.artifacts import ArtifactRef, ArtifactView
from harness.ports.board import BoardView
from harness.ports.clock import Clock
from harness.ports.control import TaskControl
from harness.ports.issue_import import IssueImport, NullIssueImport
from harness.ports.logs import StageOutputView
from harness.ports.process_admin import (
    ProcessAdmin,
    ProcessAdminValidationError,
    ProcessFields,
    ProcessNotFound,
)
from harness.ports.triggers import CheckSpec
from harness.ports.updater import Updater, UpdateError
from harness.ports.workflow_admin import WorkflowAdmin, WorkflowValidationError
from harness.ports.workflows import WorkflowNotFound


class _EmptyArtifactView(ArtifactView):
    """Empty view for a board without an artifact store. Keeps `create_app`
    backward compatible — whoever doesn't supply artifacts gets an empty list.
    It sits behind the port, so api/ still knows no driver."""

    def list(self, task_id: str) -> tuple[ArtifactRef, ...]:
        return ()

    def read(self, task_id: str, step: str, attempt: int, name: str) -> str | None:
        return None


class _EmptyStageOutputView(StageOutputView):
    """Empty stage-output view for a board wired without live output. Keeps
    `create_app` backward compatible — the panel connects but stays empty and
    closes at once. Sits behind the port, so api/ still knows no driver."""

    def tail(self, task_id: str) -> tuple[str, ...]:
        return ()

    async def subscribe(self, task_id: str) -> AsyncIterator[str]:
        return
        yield  # pragma: no cover - makes this an async generator


class _NullTaskControl(TaskControl):
    """No-op control for a board wired without one. Keeps `create_app` backward
    compatible — restart simply reports nothing was done. Behind the port, so
    api/ still knows no driver."""

    def restart(self, task_id: str) -> bool:
        return False

    def delete(self, task_id: str) -> bool:
        return False


class _EmptyAgentAdmin(AgentAdmin):
    """No-op agent admin for a board wired without one. Keeps `create_app`
    backward compatible — the admin routes exist but list nothing and refuse
    every write. Behind the port, so api/ still knows no driver."""

    def list(self) -> tuple[str, ...]:
        return ()

    def read(self, name: str) -> AgentSpec:
        raise AgentNotFound(f"agent {name!r} does not exist")

    def write(self, name: str, fields: AgentFields) -> AgentSpec:
        raise AgentValidationError({"name": "no agent admin configured"})

    def delete(self, name: str) -> bool:
        return False


class _EmptyWorkflowAdmin(WorkflowAdmin):
    """No-op workflow admin for a board wired without one. Same rationale as
    `_EmptyAgentAdmin`."""

    def list(self) -> tuple[str, ...]:
        return ()

    def read_raw(self, name: str) -> str:
        raise WorkflowNotFound(f"workflow {name!r} does not exist")

    def write_raw(self, name: str, raw_json: str) -> None:
        raise WorkflowValidationError("no workflow admin configured")

    def delete(self, name: str) -> bool:
        return False


class _EmptyProcessAdmin(ProcessAdmin):
    """No-op process admin for a board wired without one. Same rationale as
    `_EmptyAgentAdmin` — the routes exist but list nothing and refuse writes."""

    def list(self) -> tuple[str, ...]:
        return ()

    def read(self, name: str) -> ProcessFields:
        raise ProcessNotFound(f"process {name!r} does not exist")

    def write(self, name: str, fields: ProcessFields) -> ProcessFields:
        raise ProcessAdminValidationError({"name": "no process admin configured"})

    def delete(self, name: str) -> bool:
        return False

    def check_names(self) -> tuple[str, ...]:
        return ()

    def check_specs(self) -> tuple[CheckSpec, ...]:
        return ()

    def sink_kinds(self) -> tuple[str, ...]:
        return ("none",)

    def repository_names(self) -> tuple[str, ...]:
        return ()


class _NullUpdater(Updater):
    """No-op updater for a board wired without one (tests, or a from-source
    board that cannot upgrade itself). The button exists on every page but
    reports that self-update is unavailable here. Behind the port, so api/ still
    knows no driver."""

    def update(self) -> None:
        raise UpdateError("self-update is not available in this deployment")


def create_app(
    *,
    view: BoardView,
    artifacts: ArtifactView | None = None,
    output: StageOutputView | None = None,
    control: TaskControl | None = None,
    clock: Clock,
    coalesce_seconds: float = 0.25,
    agent_admin: AgentAdmin | None = None,
    workflow_admin: WorkflowAdmin | None = None,
    process_admin: ProcessAdmin | None = None,
    updater: Updater | None = None,
    issue_import: IssueImport | None = None,
    version: str = "unknown",
    build_time: str | None = None,
) -> FastAPI:
    artifacts = artifacts or _EmptyArtifactView()
    output = output or _EmptyStageOutputView()
    control = control or _NullTaskControl()
    agent_admin = agent_admin or _EmptyAgentAdmin()
    workflow_admin = workflow_admin or _EmptyWorkflowAdmin()
    process_admin = process_admin or _EmptyProcessAdmin()
    updater = updater or _NullUpdater()
    issue_import = issue_import or NullIssueImport()
    app = FastAPI(title="harness board", docs_url=None, redoc_url=None)
    app.state.view = view
    app.state.artifacts = artifacts
    app.state.output = output
    app.state.control = control
    app.state.clock = clock
    app.state.coalesce_seconds = coalesce_seconds
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )
    app.include_router(
        build_json_router(
            view, artifacts, agent_admin, workflow_admin, process_admin, version, build_time
        )
    )
    app.include_router(
        build_html_router(
            view,
            artifacts,
            output,
            control,
            clock,
            coalesce_seconds,
            agent_admin,
            workflow_admin,
            process_admin,
            updater,
            issue_import,
            version,
            build_time,
        )
    )
    return app
