import pytest

from agentharness.dispatch.retry import backoff_seconds, should_retry
from agentharness.models import RetryPolicy


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("exponential", [30.0, 60.0, 120.0]),
        ("linear", [30.0, 60.0, 90.0]),
        ("fixed", [30.0, 30.0, 30.0]),
    ],
)
def test_backoff_curves(kind, expected):
    policy = RetryPolicy(backoff=kind)
    assert [backoff_seconds(a, policy) for a in (1, 2, 3)] == expected


def test_backoff_is_capped():
    assert backoff_seconds(20, RetryPolicy(backoff="exponential"), cap=300.0) == 300.0


def test_should_retry_while_attempts_remain():
    policy = RetryPolicy(max_attempts=3)
    assert should_retry(1, policy) is True
    assert should_retry(2, policy) is True


def test_should_not_retry_at_or_beyond_max_attempts():
    policy = RetryPolicy(max_attempts=3)
    assert should_retry(3, policy) is False
    assert should_retry(4, policy) is False
