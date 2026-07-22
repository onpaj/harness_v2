"""The `Check` port and its `Observation`: the condition a trigger evaluates.

A trigger fires when something is true out in the world — a schedule elapsed,
a queue filled, a PR went stale. That *what-is-true* lives behind one verb,
`Check.evaluate()`, which returns zero or more `Observation`s: an empty list
means the condition isn't met (no task), each `Observation` is one reason to
fire. A `CheckFactory` builds a `Check` from a plain params dict, so a trigger
is data (a name, an interval, a check kind + params) rather than code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Observation:
    """One reason a `Check` fired.

    `state_key` feeds `per-state` dedup — two observations with the same key are
    the same standing reason and must not yield two tasks. `data` is shallow-merged
    into the emitted task's `data`.
    """

    state_key: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class Check(ABC):
    """A condition a trigger evaluates each tick."""

    @abstractmethod
    def evaluate(self) -> list[Observation]:
        """Empty list = condition not met (no task). Each `Observation` is one reason to fire."""


CheckFactory = Callable[[dict[str, Any]], Check]


def parse_interval(text: str) -> float:
    """Convert a duration string to seconds.

    Accepts a `s`/`m`/`h` suffix (`"45s"`→45.0, `"30m"`→1800.0, `"1h"`→3600.0)
    or a bare number as seconds (`"2"`→2.0). Raises `ValueError` on a malformed
    or non-positive value.
    """
    raw = text.strip()
    if not raw:
        raise ValueError("empty interval")

    units = {"s": 1.0, "m": 60.0, "h": 3600.0}
    suffix = raw[-1]
    if suffix in units:
        number, factor = raw[:-1], units[suffix]
    else:
        number, factor = raw, 1.0

    if not number:
        raise ValueError(f"missing number in interval: {text!r}")

    try:
        value = float(number)
    except ValueError as exc:
        raise ValueError(f"malformed interval: {text!r}") from exc

    if value <= 0:
        raise ValueError(f"interval must be positive: {text!r}")

    return value * factor
