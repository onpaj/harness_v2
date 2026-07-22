from __future__ import annotations

import pytest

from harness.ports.triggers import Check, Observation, parse_interval


def test_parse_interval_valid() -> None:
    assert parse_interval("1h") == 3600.0
    assert parse_interval("30m") == 1800.0
    assert parse_interval("45s") == 45.0
    assert parse_interval("2") == 2.0
    assert parse_interval("24h") == 86400.0


@pytest.mark.parametrize("bad", ["1x", "", "h", "-5m"])
def test_parse_interval_malformed_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_interval(bad)


def test_check_subclass_satisfies_abc() -> None:
    class FixedCheck(Check):
        def evaluate(self) -> list[Observation]:
            return [Observation(state_key="k", data={"a": 1})]

    result = FixedCheck().evaluate()
    assert result == [Observation(state_key="k", data={"a": 1})]


def test_observation_defaults() -> None:
    obs = Observation()
    assert obs.state_key is None
    assert obs.data == {}
