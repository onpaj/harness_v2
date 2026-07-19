"""Tests for the durable cron scheduler."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from agentharness.config import load_config
from agentharness.dispatch.dispatcher import Dispatcher
from agentharness.dispatch.limits import ConcurrencyLimiter, RateLimitGate
from agentharness.models import ScheduleDef
from agentharness.queue.filesystem import FilesystemQueue
from agentharness.registry.agents import AgentRegistry
from agentharness.registry.repos import RepoRegistry
from agentharness.runner.executor import ExecResult, Executor
from agentharness.runner.runner import Runner
from agentharness.scheduler.scheduler import Scheduler, next_fire
from agentharness.store.runs import RunStore

UTC = timezone.utc


class ScriptedExecutor(Executor):
    """Records prompts and always succeeds. The scheduler never runs tasks in
    these tests -- it only submits them -- but Runner needs a real executor."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, req):
        self.calls.append(req.prompt)
        return ExecResult(
            exit_code=0, is_error=False, result_text="ok", total_cost_usd=0.0, num_turns=1
        )


@pytest.fixture()
def build(home, origin_repo):
    """cfg/agents/repos/store/queue/runner/dispatcher over a throwaway home."""

    def _build():
        cfg = load_config()
        cfg.ensure_dirs()

        (cfg.agents_dir / "planner.yaml").write_text(
            yaml.safe_dump(
                {
                    "name": "planner",
                    "description": "planner agent",
                    "allowed_tools": ["Read", "Write"],
                    "can_handoff_to": [],
                    "retries": {"max_attempts": 1, "backoff": "fixed"},
                    "concurrency": 1,
                }
            )
        )

        agents = AgentRegistry.load(cfg.agents_dir)
        repos = RepoRegistry(cfg)
        repos.add("app", str(origin_repo))
        store = RunStore(cfg.db_path)
        queue = FilesystemQueue(cfg.queues_dir)
        runner = Runner(cfg, agents, repos, store, ScriptedExecutor())
        gate = RateLimitGate(cfg.rate_limit_patterns, 60.0, 480.0)
        limiter = ConcurrencyLimiter(cfg.max_concurrency)
        dispatcher = Dispatcher(cfg, agents, repos, queue, store, runner, gate, limiter)
        return dispatcher, store, queue, cfg

    return _build


def a_schedule(**kw) -> ScheduleDef:
    base = dict(
        schedule_id="nightly",
        cron="0 7 * * *",
        agent="planner",
        intent="daily plan",
        repo="app",
        payload={"scope": "everything"},
        enabled=True,
    )
    base.update(kw)
    return ScheduleDef(**base)


# --- next_fire ------------------------------------------------------------


def test_next_fire_lands_on_the_same_day_when_the_time_has_not_passed():
    after = datetime(2026, 7, 19, 6, 0, tzinfo=UTC)
    assert next_fire("0 7 * * *", after) == datetime(2026, 7, 19, 7, 0, tzinfo=UTC)


def test_next_fire_rolls_to_the_next_day_once_the_time_has_passed():
    after = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
    assert next_fire("0 7 * * *", after) == datetime(2026, 7, 20, 7, 0, tzinfo=UTC)


def test_next_fire_returns_a_timezone_aware_utc_datetime():
    fire = next_fire("*/5 * * * *", datetime(2026, 7, 19, 6, 1, tzinfo=UTC))
    assert fire.tzinfo is not None
    assert fire.utcoffset() == timedelta(0)


def test_next_fire_rejects_an_invalid_cron_expression():
    with pytest.raises(ValueError):
        next_fire("not a cron", datetime(2026, 7, 19, 6, 0, tzinfo=UTC))


# --- add / remove / list --------------------------------------------------


def test_add_rejects_an_invalid_cron_expression(build):
    dispatcher, store, *_ = build()
    scheduler = Scheduler(store, dispatcher)

    with pytest.raises(ValueError):
        scheduler.add(a_schedule(cron="every tuesday please"))

    assert scheduler.list() == []


def test_add_persists_and_survives_a_fresh_scheduler_over_the_same_store(build):
    dispatcher, store, *_ = build()
    Scheduler(store, dispatcher).add(a_schedule())

    reopened = Scheduler(RunStore(store.db_path), dispatcher)
    listed = reopened.list()

    assert [s.schedule_id for s in listed] == ["nightly"]
    assert listed[0].cron == "0 7 * * *"
    assert listed[0].agent == "planner"
    assert listed[0].payload == {"scope": "everything"}


def test_remove_deletes_the_schedule(build):
    dispatcher, store, *_ = build()
    scheduler = Scheduler(store, dispatcher)
    scheduler.add(a_schedule())

    scheduler.remove("nightly")

    assert scheduler.list() == []


# --- tick -----------------------------------------------------------------


def test_tick_before_the_fire_time_submits_nothing(build):
    dispatcher, store, queue, _ = build()
    scheduler = Scheduler(store, dispatcher)
    scheduler.add(a_schedule(), now=datetime(2026, 7, 19, 6, 0, tzinfo=UTC))

    assert scheduler.tick(now=datetime(2026, 7, 19, 6, 59, tzinfo=UTC)) == []
    assert queue.depth("planner") == 0


def test_tick_at_the_fire_time_submits_exactly_one_stamped_task(build):
    dispatcher, store, queue, _ = build()
    scheduler = Scheduler(store, dispatcher)
    scheduler.add(a_schedule(), now=datetime(2026, 7, 19, 6, 0, tzinfo=UTC))

    tasks = scheduler.tick(now=datetime(2026, 7, 19, 7, 0, tzinfo=UTC))

    assert len(tasks) == 1
    task = tasks[0]
    assert task.schedule_id == "nightly"
    assert task.agent == "planner"
    assert task.intent == "daily plan"
    assert task.repo == "app"
    assert task.payload == {"scope": "everything"}
    assert queue.depth("planner") == 1


def test_a_second_tick_immediately_after_firing_submits_nothing(build):
    dispatcher, store, queue, _ = build()
    scheduler = Scheduler(store, dispatcher)
    scheduler.add(a_schedule(), now=datetime(2026, 7, 19, 6, 0, tzinfo=UTC))
    now = datetime(2026, 7, 19, 7, 0, tzinfo=UTC)

    assert len(scheduler.tick(now=now)) == 1
    assert scheduler.tick(now=now) == []
    assert queue.depth("planner") == 1


def test_a_disabled_schedule_never_fires(build):
    dispatcher, store, queue, _ = build()
    scheduler = Scheduler(store, dispatcher)
    scheduler.add(
        a_schedule(enabled=False), now=datetime(2026, 7, 19, 6, 0, tzinfo=UTC)
    )

    assert scheduler.tick(now=datetime(2026, 7, 19, 7, 0, tzinfo=UTC)) == []
    assert scheduler.tick(now=datetime(2026, 7, 25, 7, 0, tzinfo=UTC)) == []
    assert queue.depth("planner") == 0


def test_a_long_past_schedule_fires_once_not_once_per_missed_interval(build):
    dispatcher, store, queue, _ = build()
    scheduler = Scheduler(store, dispatcher)
    # Simulate a month of downtime: next_fire_at is 30 daily fires in the past.
    store.upsert_schedule(a_schedule(), datetime(2026, 6, 19, 7, 0, tzinfo=UTC))
    now = datetime(2026, 7, 19, 9, 30, tzinfo=UTC)

    tasks = scheduler.tick(now=now)

    assert len(tasks) == 1
    assert queue.depth("planner") == 1
    # And the catch-up leaves next_fire_at in the future, so the very next tick
    # is quiet rather than replaying the remaining backlog.
    assert scheduler.tick(now=now) == []
    assert queue.depth("planner") == 1


def test_catching_up_schedules_the_next_fire_relative_to_now(build):
    dispatcher, store, *_ = build()
    scheduler = Scheduler(store, dispatcher)
    store.upsert_schedule(a_schedule(), datetime(2026, 6, 19, 7, 0, tzinfo=UTC))
    now = datetime(2026, 7, 19, 9, 30, tzinfo=UTC)

    scheduler.tick(now=now)

    due_now = store.due_schedules(datetime(2026, 7, 20, 6, 59, tzinfo=UTC))
    due_later = store.due_schedules(datetime(2026, 7, 20, 7, 0, tzinfo=UTC))
    assert due_now == []
    assert [s.schedule_id for s, _ in due_later] == ["nightly"]


def test_tick_fires_every_due_schedule(build):
    dispatcher, store, queue, _ = build()
    scheduler = Scheduler(store, dispatcher)
    at_six = datetime(2026, 7, 19, 6, 0, tzinfo=UTC)
    scheduler.add(a_schedule(schedule_id="a", cron="0 7 * * *"), now=at_six)
    scheduler.add(a_schedule(schedule_id="b", cron="30 6 * * *"), now=at_six)

    tasks = scheduler.tick(now=datetime(2026, 7, 19, 7, 0, tzinfo=UTC))

    assert sorted(t.schedule_id for t in tasks) == ["a", "b"]
    assert queue.depth("planner") == 2


def test_tick_defaults_now_to_the_current_time(build):
    dispatcher, store, queue, _ = build()
    scheduler = Scheduler(store, dispatcher)
    # Due a minute ago in real wall-clock terms.
    store.upsert_schedule(
        a_schedule(cron="*/5 * * * *"),
        datetime.now(UTC) - timedelta(minutes=1),
    )

    tasks = scheduler.tick()

    assert len(tasks) == 1
    assert queue.depth("planner") == 1


def test_a_failing_submit_does_not_abort_the_other_due_schedules(build):
    dispatcher, store, queue, _ = build()
    scheduler = Scheduler(store, dispatcher)
    at_six = datetime(2026, 7, 19, 6, 0, tzinfo=UTC)
    scheduler.add(a_schedule(schedule_id="ghost", agent="nobody"), now=at_six)
    scheduler.add(a_schedule(schedule_id="real"), now=at_six)

    tasks = scheduler.tick(now=datetime(2026, 7, 19, 7, 0, tzinfo=UTC))

    assert [t.schedule_id for t in tasks] == ["real"]
    assert queue.depth("planner") == 1
    kinds = [e["kind"] for e in store.recent_events()]
    assert "schedule.error" in kinds


def test_firing_records_an_event_naming_the_schedule(build):
    dispatcher, store, *_ = build()
    scheduler = Scheduler(store, dispatcher)
    scheduler.add(a_schedule(), now=datetime(2026, 7, 19, 6, 0, tzinfo=UTC))

    scheduler.tick(now=datetime(2026, 7, 19, 7, 0, tzinfo=UTC))

    fired = [e for e in store.recent_events() if e["kind"] == "schedule.fired"]
    assert len(fired) == 1
    assert fired[0]["data"]["schedule_id"] == "nightly"


async def test_run_forever_ticks_until_stopped(build):
    dispatcher, store, queue, _ = build()
    scheduler = Scheduler(store, dispatcher)
    store.upsert_schedule(
        a_schedule(cron="*/5 * * * *"), datetime.now(UTC) - timedelta(minutes=1)
    )

    loop = asyncio.ensure_future(scheduler.run_forever(interval=0.01))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if queue.depth("planner") >= 1:
            break
    scheduler.stop()
    await asyncio.wait_for(loop, timeout=2.0)

    assert queue.depth("planner") == 1
