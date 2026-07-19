"""Skutečný čas."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from harness.ports.clock import Clock


class SystemClock(Clock):
    def now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
