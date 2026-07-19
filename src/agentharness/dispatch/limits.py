"""Concurrency ceiling and rate-limit backpressure.

Subscription usage is the binding constraint on this platform, so the harness
caps simultaneous `claude -p` processes globally and pauses dispatch entirely
when the plan's ceiling is hit, rather than hammering it. Queues absorb the
backlog; nothing is lost.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

from agentharness.runner.executor import ExecResult


class RateLimitGate:
    def __init__(
        self,
        patterns: list[str],
        initial_backoff: float = 60.0,
        max_backoff: float = 3600.0,
    ) -> None:
        self._patterns = [p.lower() for p in patterns]
        self._initial = initial_backoff
        self._max = max_backoff
        self.backoff = initial_backoff
        self._paused = False

    @property
    def paused(self) -> bool:
        return self._paused

    def detect(self, exec_result: ExecResult) -> bool:
        """A throttle looks like an error whose text matches a known pattern.

        Only errored runs qualify -- an agent merely *writing about* rate limits
        must not pause the whole harness.
        """
        if not exec_result.is_error:
            return False
        haystack = f"{exec_result.result_text or ''}\n{exec_result.stderr or ''}".lower()
        return any(p in haystack for p in self._patterns)

    def trip(self) -> None:
        if self._paused:
            self.backoff = min(self.backoff * 2, self._max)
        self._paused = True

    def clear(self) -> None:
        self._paused = False
        self.backoff = self._initial

    async def wait_until_clear(
        self,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Sleep out the current backoff, then resume automatically.

        Recovery needs no operator action; an overnight run survives a limit.
        """
        if not self._paused:
            return
        await sleep(self.backoff)
        self._paused = False


class ConcurrencyLimiter:
    def __init__(self, global_limit: int) -> None:
        self.global_limit = global_limit
        self._global = asyncio.Semaphore(global_limit)
        self._per_agent: dict[str, asyncio.Semaphore] = {}
        self._active: dict[str, int] = defaultdict(int)

    def _agent_sem(self, agent: str, agent_limit: int) -> asyncio.Semaphore:
        if agent not in self._per_agent:
            self._per_agent[agent] = asyncio.Semaphore(agent_limit)
        return self._per_agent[agent]

    @asynccontextmanager
    async def slot(self, agent: str, agent_limit: int):
        """Acquire the agent semaphore, then the global one -- always in that
        order, so two agents can never deadlock against each other."""
        agent_sem = self._agent_sem(agent, agent_limit)
        await agent_sem.acquire()
        try:
            await self._global.acquire()
        except BaseException:
            agent_sem.release()
            raise
        self._active[agent] += 1
        try:
            yield
        finally:
            self._active[agent] -= 1
            self._global.release()
            agent_sem.release()

    def active(self, agent: str) -> int:
        return self._active[agent]

    def active_total(self) -> int:
        return sum(self._active.values())

    def has_capacity(self) -> bool:
        return self.active_total() < self.global_limit
