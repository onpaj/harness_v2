"""Retry and backoff policy."""

from __future__ import annotations

from agentharness.models import RetryPolicy


def should_retry(attempt: int, policy: RetryPolicy) -> bool:
    return attempt < policy.max_attempts


def backoff_seconds(
    attempt: int,
    policy: RetryPolicy,
    base: float = 30.0,
    cap: float = 3600.0,
) -> float:
    """Delay before attempt N+1, given that attempt N just failed."""
    if policy.backoff == "fixed":
        delay = base
    elif policy.backoff == "linear":
        delay = base * attempt
    else:  # exponential
        delay = base * (2 ** (attempt - 1))
    return min(delay, cap)
