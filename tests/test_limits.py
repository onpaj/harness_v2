import asyncio

import pytest

from agentharness.dispatch.limits import ConcurrencyLimiter, RateLimitGate
from agentharness.runner.executor import ExecResult

PATTERNS = ["rate limit", "429", "usage limit"]


def gate(**over) -> RateLimitGate:
    base = dict(patterns=PATTERNS, initial_backoff=60.0, max_backoff=480.0)
    base.update(over)
    return RateLimitGate(**base)


def err(**over) -> ExecResult:
    base = dict(exit_code=1, is_error=True)
    base.update(over)
    return ExecResult(**base)


@pytest.mark.parametrize("text", ["Rate Limit exceeded", "HTTP 429", "usage limit reached"])
def test_detect_matches_known_throttles_case_insensitively(text):
    assert gate().detect(err(result_text=text)) is True


def test_detect_reads_stderr_too():
    assert gate().detect(err(stderr="429 Too Many Requests")) is True


def test_detect_ignores_unrelated_errors():
    assert gate().detect(err(result_text="file not found")) is False


def test_detect_ignores_successful_runs_that_merely_mention_limits():
    """An agent writing docs about rate limits must not pause the harness."""
    result = ExecResult(exit_code=0, is_error=False, result_text="I documented the rate limit policy")
    assert gate().detect(result) is False


def test_trip_pauses_dispatch():
    g = gate()
    assert g.paused is False
    g.trip()
    assert g.paused is True


def test_repeated_trips_double_the_backoff_up_to_the_cap():
    g = gate()
    g.trip()
    assert g.backoff == 60.0
    g.trip()
    assert g.backoff == 120.0
    g.trip()
    assert g.backoff == 240.0
    g.trip()
    g.trip()
    assert g.backoff == 480.0, "backoff must not exceed max_backoff"


def test_clear_resets_pause_and_backoff():
    g = gate()
    g.trip()
    g.trip()
    g.clear()
    assert g.paused is False
    assert g.backoff == 60.0


async def test_wait_returns_immediately_when_not_paused():
    slept = []
    await gate().wait_until_clear(sleep=lambda s: _record(slept, s))
    assert slept == []


async def test_wait_sleeps_the_backoff_then_resumes_without_an_operator():
    g = gate()
    g.trip()
    slept = []
    await g.wait_until_clear(sleep=lambda s: _record(slept, s))

    assert slept == [60.0]
    assert g.paused is False


async def _record(bucket, seconds):
    bucket.append(seconds)


async def test_limiter_enforces_the_global_ceiling():
    limiter = ConcurrencyLimiter(global_limit=2)
    peak = 0
    current = 0

    async def worker():
        nonlocal peak, current
        async with limiter.slot("a", agent_limit=10):
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.01)
            current -= 1

    await asyncio.gather(*(worker() for _ in range(6)))
    assert peak == 2


async def test_per_agent_limit_binds_below_the_global_one():
    limiter = ConcurrencyLimiter(global_limit=5)
    peak = 0
    current = 0

    async def worker():
        nonlocal peak, current
        async with limiter.slot("solo", agent_limit=1):
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.01)
            current -= 1

    await asyncio.gather(*(worker() for _ in range(4)))
    assert peak == 1


async def test_releasing_a_slot_lets_a_waiter_through():
    limiter = ConcurrencyLimiter(global_limit=1)
    order = []

    async def worker(name):
        async with limiter.slot(name, agent_limit=1):
            order.append(name)
            await asyncio.sleep(0.01)

    await asyncio.gather(worker("first"), worker("second"))
    assert sorted(order) == ["first", "second"]


async def test_active_counts_track_occupancy():
    limiter = ConcurrencyLimiter(global_limit=3)

    async with limiter.slot("a", agent_limit=2):
        assert limiter.active("a") == 1
        assert limiter.active_total() == 1
        async with limiter.slot("b", agent_limit=2):
            assert limiter.active_total() == 2

    assert limiter.active_total() == 0
    assert limiter.has_capacity() is True
