"""Durable cron scheduler.

State lives in the run store, not in memory, so a restart resumes exactly where
the last process left off. The one rule that shapes the whole design: a schedule
that came due while the harness was down fires *once* on the way back up, never
once per interval missed. That is why every advance is computed from `now`
rather than from the stale `next_fire_at` — catching up on a month of nightly
runs is noise, not fidelity.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from croniter import CroniterBadCronError, croniter

from agentharness.dispatch.dispatcher import Dispatcher
from agentharness.models import ScheduleDef, Task
from agentharness.store.runs import RunStore


def next_fire(cron: str, after: datetime) -> datetime:
    """The first firing time strictly after `after`.

    Raises ValueError on an unparseable cron expression. Naive datetimes are
    read as UTC; the result is always timezone-aware UTC.
    """
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    after = after.astimezone(timezone.utc)

    try:
        it = croniter(cron, after)
    except (CroniterBadCronError, ValueError, KeyError) as exc:
        raise ValueError(f"invalid cron expression {cron!r}: {exc}") from exc

    fire: datetime = it.get_next(datetime)
    if fire.tzinfo is None:
        fire = fire.replace(tzinfo=timezone.utc)
    return fire.astimezone(timezone.utc)


class Scheduler:
    """Cron-driven producer of root tasks."""

    def __init__(self, store: RunStore, dispatcher: Dispatcher) -> None:
        self.store = store
        self.dispatcher = dispatcher
        self._stopping = False

    # -- definition management -------------------------------------------

    def add(self, schedule: ScheduleDef, *, now: datetime | None = None) -> None:
        """Validate, compute the first firing time, and persist.

        `now` is an injection point for tests; production callers omit it.
        """
        moment = self._now(now)
        # next_fire validates the expression, so a bad cron never reaches the DB.
        self.store.upsert_schedule(schedule, next_fire(schedule.cron, moment))

    def remove(self, schedule_id: str) -> None:
        self.store.delete_schedule(schedule_id)

    def list(self) -> list[ScheduleDef]:
        return self.store.list_schedules()

    # -- firing ------------------------------------------------------------

    def tick(self, now: datetime | None = None) -> list[Task]:
        """Fire every due schedule exactly once and re-arm it from `now`."""
        moment = self._now(now)
        fired: list[Task] = []

        for schedule, _due_at in self.store.due_schedules(moment):
            try:
                task = self.dispatcher.submit(
                    schedule.agent,
                    schedule.intent,
                    repo=schedule.repo,
                    payload=dict(schedule.payload),
                    schedule_id=schedule.schedule_id,
                )
            except Exception as exc:  # noqa: BLE001 -- one bad schedule must not
                # stall the rest, and it must not spin: re-arm it either way.
                self._rearm(schedule, moment)
                self.store.event(
                    "schedule.error",
                    agent=schedule.agent,
                    data={
                        "schedule_id": schedule.schedule_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                continue

            self._rearm(schedule, moment)
            self.store.event(
                "schedule.fired",
                task_id=task.task_id,
                trace_id=task.trace_id,
                agent=schedule.agent,
                data={"schedule_id": schedule.schedule_id, "intent": schedule.intent},
            )
            fired.append(task)

        return fired

    def _rearm(self, schedule: ScheduleDef, moment: datetime) -> None:
        """Advance from `moment`, collapsing any missed intervals into one fire."""
        self.store.mark_fired(
            schedule.schedule_id, next_fire(schedule.cron, moment), moment
        )

    # -- daemon ------------------------------------------------------------

    def stop(self) -> None:
        self._stopping = True

    async def run_forever(self, interval: float = 30.0) -> None:
        self._stopping = False
        while not self._stopping:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 -- the loop must not die
                self.store.event("schedule.error", data={"error": str(exc)})
            await asyncio.sleep(interval)

    @staticmethod
    def _now(now: datetime | None) -> datetime:
        if now is None:
            return datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)
