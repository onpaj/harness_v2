"""Factory API. BoardView přichází zvenčí — proto o driverech neví."""

from __future__ import annotations

from fastapi import FastAPI

from harness.api.routes import build_json_router
from harness.ports.board import BoardView
from harness.ports.clock import Clock


def create_app(
    *, view: BoardView, clock: Clock, coalesce_seconds: float = 0.25
) -> FastAPI:
    app = FastAPI(title="harness board", docs_url=None, redoc_url=None)
    app.state.view = view
    app.state.clock = clock
    app.state.coalesce_seconds = coalesce_seconds
    app.include_router(build_json_router(view))
    return app
