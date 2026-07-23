"""API factory. BoardView comes from outside — so it knows nothing about drivers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from harness.api.process_routes import build_process_router
from harness.api.routes import build_html_router, build_json_router
from harness.ports.artifacts import ArtifactRef, ArtifactView
from harness.ports.board import BoardView
from harness.ports.clock import Clock
from harness.ports.control import TaskControl
from harness.ports.logs import StageOutputView
from harness.ports.processes import ProcessAdmin
from harness.ports.repos import RepositoryNotFound, RepositoryRegistry
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


class _NullProcessAdmin(ProcessAdmin):
    """No-op process admin for a board wired without one. Keeps `create_app`
    backward compatible — the editor lists no processes and refuses to load
    or save one. Behind the port, so api/ still knows no driver."""

    def list_processes(self) -> list[str]:
        return []

    def load_process(self, name: str):
        raise WorkflowNotFound(f"process {name!r} does not exist")

    def save_repositories(self, name: str, repositories) -> None:
        raise WorkflowNotFound(f"process {name!r} does not exist")


class _EmptyRepositoryRegistry(RepositoryRegistry):
    """Empty registry for a board wired without one. Keeps `create_app`
    backward compatible — no repos to offer in the multiselect."""

    def resolve(self, name: str):
        raise RepositoryNotFound(f"repo {name!r} is not in the registry")

    def names(self) -> list[str]:
        return []


def create_app(
    *,
    view: BoardView,
    artifacts: ArtifactView | None = None,
    output: StageOutputView | None = None,
    control: TaskControl | None = None,
    processes: ProcessAdmin | None = None,
    repos: RepositoryRegistry | None = None,
    clock: Clock,
    coalesce_seconds: float = 0.25,
) -> FastAPI:
    artifacts = artifacts or _EmptyArtifactView()
    output = output or _EmptyStageOutputView()
    control = control or _NullTaskControl()
    processes = processes or _NullProcessAdmin()
    repos = repos or _EmptyRepositoryRegistry()
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
    app.include_router(build_json_router(view, artifacts))
    app.include_router(
        build_html_router(view, artifacts, output, control, clock, coalesce_seconds)
    )
    app.include_router(build_process_router(processes, repos))
    return app
