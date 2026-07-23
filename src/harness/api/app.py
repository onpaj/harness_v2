"""API factory. BoardView comes from outside — so it knows nothing about drivers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from harness.api.routes import build_html_router, build_json_router
from harness.ports.artifacts import ArtifactRef, ArtifactView
from harness.ports.board import BoardView
from harness.ports.clock import Clock
from harness.ports.control import TaskControl
from harness.ports.logs import StageOutputView


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


def create_app(
    *,
    view: BoardView,
    artifacts: ArtifactView | None = None,
    output: StageOutputView | None = None,
    control: TaskControl | None = None,
    clock: Clock,
    coalesce_seconds: float = 0.25,
) -> FastAPI:
    artifacts = artifacts or _EmptyArtifactView()
    output = output or _EmptyStageOutputView()
    control = control or _NullTaskControl()
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
    return app
