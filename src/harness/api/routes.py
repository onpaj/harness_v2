"""Endpointy. Vidí výhradně port BoardView."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException

from harness.ports.board import BoardView
from harness.ports.clock import Clock


async def board_event_stream(
    view: BoardView, clock: Clock, coalesce_seconds: float
) -> AsyncIterator[str]:
    """SSE rámce. Nesou jen číslo revize — data si klient natáhne sám.

    Po každém rámci se čeká coalesce_seconds; změny, které mezitím
    nastanou, se slijí do jedné notifikace. Bez toho by pět consumerů
    dokázalo tepat překreslováním.
    """
    async for revision in view.subscribe():
        yield f"event: board\ndata: {json.dumps({'revision': revision})}\n\n"
        await clock.sleep(coalesce_seconds)


def build_json_router(view: BoardView) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/board")
    def board() -> dict:
        return view.snapshot().to_dict()

    @router.get("/tasks/{task_id}")
    def task(task_id: str) -> dict:
        found = view.get(task_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"task {task_id} neexistuje")
        return found.to_dict()

    return router
