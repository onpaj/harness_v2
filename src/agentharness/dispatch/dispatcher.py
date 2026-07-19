"""The reactive loop.

Leases tasks per agent within the concurrency ceiling, runs them, routes their
handoffs, and merges a trace's leaf branches once nothing of that trace is still
in flight. Failure is policy, not exception: retry with backoff, dead-letter at
the limit, pause globally on a rate limit.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from agentharness.config import Config
from agentharness.dispatch.limits import ConcurrencyLimiter, RateLimitGate
from agentharness.dispatch.retry import backoff_seconds, should_retry
from agentharness.dispatch.routing import route_handoffs

from agentharness.git.merge import MergeConflict, merge_leaves
from agentharness.ids import new_task_id, new_trace_id
from agentharness.models import Task, TaskArtifacts
from agentharness.queue.base import Queue
from agentharness.registry.agents import AgentRegistry
from agentharness.registry.repos import RepoRegistry
from agentharness.runner.runner import Runner
from agentharness.store.runs import RunStore


class Dispatcher:
    def __init__(
        self,
        cfg: Config,
        agents: AgentRegistry,
        repos: RepoRegistry,
        queue: Queue,
        store: RunStore,
        runner: Runner,
        gate: RateLimitGate | None = None,
        limiter: ConcurrencyLimiter | None = None,
    ) -> None:
        self.cfg = cfg
        self.agents = agents
        self.repos = repos
        self.queue = queue
        self.store = store
        self.runner = runner
        self.gate = gate or RateLimitGate(
            cfg.rate_limit_patterns,
            cfg.rate_limit_initial_backoff_seconds,
            cfg.rate_limit_max_backoff_seconds,
        )
        self.limiter = limiter or ConcurrencyLimiter(cfg.max_concurrency)
        self._stopping = False
        self._inflight: set[asyncio.Task] = set()

    # -- producer side --------------------------------------------------

    def submit(
        self,
        agent: str,
        intent: str,
        *,
        repo: str | None = None,
        payload: dict | None = None,
        priority: int = 5,
        schedule_id: str | None = None,
    ) -> Task:
        """Mint and enqueue a root task. Every producer goes through here."""
        if agent not in self.agents.names():
            raise KeyError(f"no such agent {agent!r}")

        trace_id = new_trace_id()
        task = Task(
            task_id=new_task_id(),
            trace_id=trace_id,
            parent_task_id=None,
            agent=agent,
            repo=repo,
            intent=intent,
            payload=payload or {},
            artifacts=TaskArtifacts(),
            idempotency_key=f"root:{trace_id}",
            priority=priority,
            created_at=datetime.now(timezone.utc),
            schedule_id=schedule_id,
        )
        self.queue.enqueue(task)
        self.store.record_task(task, status="pending")
        self.store.event(
            "task.enqueued", task_id=task.task_id, trace_id=trace_id, agent=agent,
            data={"intent": intent, "root": True},
        )
        return task

    # -- consumer side --------------------------------------------------

    async def tick(self) -> int:
        """One poll pass. Returns how many tasks were dispatched."""
        for task in self.queue.reclaim_expired():
            self.store.set_task_status(task.task_id, "pending")
            self.store.event("task.lease_expired", task_id=task.task_id, trace_id=task.trace_id)
        self.queue.promote_delayed()

        await self.gate.wait_until_clear()
        if self.gate.paused:
            return 0

        dispatched = 0
        for agent_name in self.queue.agents_with_work():
            try:
                agent = self.agents.get(agent_name)
            except KeyError:
                continue

            while self.limiter.has_capacity() and self.limiter.active(agent_name) < agent.concurrency:
                task = self.queue.lease(agent_name, self.cfg.lease_timeout_seconds)
                if task is None:
                    break
                self.store.set_task_status(task.task_id, "leased")
                self._spawn(self.handle_task(task))
                dispatched += 1

        return dispatched

    def _spawn(self, coro) -> None:
        job = asyncio.ensure_future(coro)
        self._inflight.add(job)
        job.add_done_callback(self._inflight.discard)

    async def drain(self) -> None:
        """Await every in-flight run. Tests use this instead of sleeping."""
        while self._inflight:
            done = await asyncio.gather(*list(self._inflight), return_exceptions=True)
            for outcome in done:
                if isinstance(outcome, BaseException):
                    # A crash in the dispatch path must never vanish silently.
                    self.store.event(
                        "dispatch.error",
                        data={"error": f"{type(outcome).__name__}: {outcome}"},
                    )

    async def handle_task(self, task: Task) -> None:
        agent = self.agents.get(task.agent)
        async with self.limiter.slot(task.agent, agent.concurrency):
            self.store.set_task_status(task.task_id, "running")
            outcome = await asyncio.to_thread(self.runner.execute, task)

        status = outcome.run.status
        result = outcome.result

        if status == "ok" and result is not None and result.status == "needs_input":
            self.queue.ack(task)
            self.store.set_task_status(task.task_id, "blocked")
            self.store.event("task.blocked", task_id=task.task_id, trace_id=task.trace_id)
            await self._maybe_complete_trace(task)
            return

        if status == "ok":
            self._route(task, agent, outcome)
            self.queue.ack(task)
            self.store.set_task_status(task.task_id, "done")
            await self._maybe_complete_trace(task)
            return

        await self._handle_failure(task, agent, outcome)

    def _route(self, task, agent, outcome) -> None:
        if outcome.result is None or not outcome.run.output_ref:
            return
        for routed in route_handoffs(
            task, agent, outcome.result, outcome.run.output_ref, self.agents
        ):
            if routed.accepted and routed.task is not None:
                if self.queue.enqueue(routed.task):
                    self.store.record_task(routed.task, status="pending")
                    self.store.record_handoff(
                        task.task_id, routed.task.task_id, routed.handoff.agent, True
                    )
                    self.store.event(
                        "handoff.accepted",
                        task_id=routed.task.task_id,
                        trace_id=task.trace_id,
                        agent=routed.handoff.agent,
                    )
            else:
                self.store.record_handoff(
                    task.task_id, None, routed.handoff.agent, False, routed.reason
                )
                self.store.event(
                    "handoff.rejected",
                    task_id=task.task_id,
                    trace_id=task.trace_id,
                    agent=routed.handoff.agent,
                    data={"reason": routed.reason},
                )

    async def _handle_failure(self, task, agent, outcome) -> None:
        exec_text = outcome.error or ""

        if outcome.exec_result is not None and self.gate.detect(outcome.exec_result):
            # Not the task's fault: requeue without burning an attempt.
            self.gate.trip()
            self.queue.nack(task, requeue=True, delay_seconds=0.0)
            self.store.set_task_status(task.task_id, "pending")
            self.store.event(
                "dispatch.paused", task_id=task.task_id, trace_id=task.trace_id,
                data={"reason": "rate limited", "backoff": self.gate.backoff},
            )
            return

        if should_retry(task.attempt, agent.retries):
            delay = backoff_seconds(
                task.attempt,
                agent.retries,
                base=self.cfg.retry_base_seconds,
                cap=self.cfg.retry_max_backoff_seconds,
            )
            self.queue.nack(task, requeue=True, delay_seconds=delay)
            self.store.set_task_status(task.task_id, "pending")
            self.store.event(
                "task.retry", task_id=task.task_id, trace_id=task.trace_id,
                data={"attempt": task.attempt, "delay": delay, "error": exec_text},
            )
            return

        self.queue.dead_letter(task, exec_text or "run failed")
        self.store.set_task_status(task.task_id, "dead")
        self.store.event(
            "task.dead_lettered", task_id=task.task_id, trace_id=task.trace_id,
            data={"error": exec_text},
        )
        await self._maybe_complete_trace(task)

    # -- trace completion -----------------------------------------------

    async def _maybe_complete_trace(self, task: Task) -> None:
        if self.store.trace_open_count(task.trace_id) > 0:
            return

        branches = self.store.trace_leaf_branches(task.trace_id)
        if not branches:
            return

        repo = self.repos.resolve(task.repo)
        mirror = self.repos.mirror_path(repo.repo_id)

        try:
            # merge_leaves takes the repo lock itself; wrapping it in another
            # would self-deadlock, since flock contends across file descriptors
            # even within one process.
            merge_ref = await asyncio.to_thread(
                merge_leaves, mirror, branches, repo, self.cfg
            )
        except MergeConflict as exc:
            self.store.event(
                "trace.merge_conflict",
                trace_id=task.trace_id,
                data={"branch": exc.branch, "files": exc.files, "branches": branches},
            )
            return

        self.store.trace_merged(task.trace_id, merge_ref)
        self.store.event(
            "trace.merged", trace_id=task.trace_id,
            data={"merge_ref": merge_ref, "branches": branches},
        )

    # -- daemon ---------------------------------------------------------

    def stop(self) -> None:
        self._stopping = True

    async def run_forever(self) -> None:
        self._stopping = False
        while not self._stopping:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 -- the loop must not die
                self.store.event("dispatch.error", data={"error": str(exc)})
            await asyncio.sleep(self.cfg.poll_interval_seconds)
        await self.drain()
