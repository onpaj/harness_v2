"""Factory API. BoardView přichází zvenčí — proto o driverech neví."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from harness.api.routes import build_html_router, build_json_router
from harness.ports.artifacts import ArtifactRef, ArtifactView
from harness.ports.board import BoardView
from harness.ports.clock import Clock


class _EmptyArtifactView(ArtifactView):
    """Prázdný pohled pro board bez úložiště artefaktů. Drží `create_app`
    zpětně kompatibilní — kdo artefakty nevkládá, dostane prázdný seznam.
    Sedí za portem, takže api/ dál nezná žádný driver."""

    def list(self, task_id: str) -> tuple[ArtifactRef, ...]:
        return ()

    def read(self, task_id: str, step: str, attempt: int, name: str) -> str | None:
        return None


def create_app(
    *,
    view: BoardView,
    artifacts: ArtifactView | None = None,
    clock: Clock,
    coalesce_seconds: float = 0.25,
) -> FastAPI:
    artifacts = artifacts or _EmptyArtifactView()
    app = FastAPI(title="harness board", docs_url=None, redoc_url=None)
    app.state.view = view
    app.state.artifacts = artifacts
    app.state.clock = clock
    app.state.coalesce_seconds = coalesce_seconds
    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )
    app.include_router(build_json_router(view, artifacts))
    app.include_router(build_html_router(view, artifacts, clock, coalesce_seconds))
    return app
