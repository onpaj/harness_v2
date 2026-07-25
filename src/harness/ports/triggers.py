"""The `Check` port and its `Observation`: the condition a trigger evaluates.

A trigger fires when something is true out in the world — a schedule elapsed,
a queue filled, a PR went stale. That *what-is-true* lives behind one verb,
`Check.evaluate()`, which returns zero or more `Observation`s: an empty list
means the condition isn't met (no task), each `Observation` is one reason to
fire. A `CheckFactory` builds a `Check` from a plain params dict, so a trigger
is data (a name, an interval, a check kind + params) rather than code.

A trigger's cadence is either a plain interval (`parse_interval`) or a
standard 5-field cron expression (`parse_cron`/`CronSchedule`) — the cron twin
of `parse_interval`, evaluated in UTC only (no per-trigger timezone yet, see
CLAUDE.md). Both produce an occurrence identity `ScheduledTrigger` gates on;
neither reads the wall clock or sleeps, so both are `FakeClock`-testable with
literal ISO strings.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable


@dataclass(frozen=True)
class Observation:
    """One reason a `Check` fired.

    `state_key` feeds `per-state` dedup — two observations with the same key are
    the same standing reason and must not yield two tasks. `data` is shallow-merged
    into the emitted task's `data`. `repository` names the repo the emitted task
    belongs to (a multi-repo check stamps it per issue); when None the trigger's
    own `repository` is used.
    """

    state_key: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    repository: str | None = None


class Check(ABC):
    """A condition a trigger evaluates each tick."""

    @abstractmethod
    def evaluate(self) -> list[Observation]:
        """Empty list = condition not met (no task). Each `Observation` is one reason to fire."""


CheckFactory = Callable[[dict[str, Any]], Check]


@dataclass(frozen=True)
class ParamSpec:
    """One input parameter an action (`Check`) declares as data.

    The UI renders a form control from this alone — nothing about a check is
    hardcoded in a template. `type` is `"text"` or `"number"` (a number is
    written into the params JSON as a JSON number, not a string). `required`
    only labels the field; validation stays with the check's own factory, which
    already raises on a missing/mistyped param.
    """

    key: str
    label: str
    type: str = "text"  # "text" | "number"
    required: bool = False
    placeholder: str = ""
    hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "placeholder": self.placeholder,
            "hint": self.hint,
        }


@dataclass(frozen=True)
class CheckSpec:
    """The declarative definition of an action: its display metadata and the
    parameters it accepts. This is the single source of truth the UI interprets
    — every action carries one, so the form never hardcodes anything per action.
    An action with no parameters (`params == ()`) is a fully-defined action too,
    not an unknown one — the form renders "no settings needed", never the
    raw-JSON fallback."""

    name: str
    label: str
    description: str = ""
    params: tuple[ParamSpec, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "params": [param.to_dict() for param in self.params],
        }


@dataclass(frozen=True)
class CheckDefinition:
    """An action definition: the declarative `spec` bundled with the `factory`
    that builds the `Check`. It is *callable* — a `CheckDefinition` IS a
    `CheckFactory` — so every existing `checks[name](params)` call site works
    unchanged, while `spec` carries the parameter definition the UI needs. This
    is how "everything is defined in that action definition" (the spec) travels
    with the code that runs the action (the factory)."""

    spec: CheckSpec
    factory: CheckFactory

    def __call__(self, params: dict[str, Any]) -> Check:
        return self.factory(params)


def check_spec_of(name: str, factory: CheckFactory) -> CheckSpec:
    """The declared `CheckSpec` for a registry entry, or a generic fallback for
    a bare factory carrying none (a hand-registered lambda, a test double). The
    fallback names the action after its registry key with no parameters — the
    UI still renders it as an ordinary card, exactly as it always could for a
    check it didn't recognize."""
    spec = getattr(factory, "spec", None)
    return spec if isinstance(spec, CheckSpec) else CheckSpec(name=name, label=name)


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


_LOOKBACK_DAYS = 4 * 366
"""Shared bound for both directions cron occurrence math walks a calendar day
at a time: `parse_cron`'s forward existence scan (from a fixed reference date)
and `CronSchedule.occurrence_at_or_before`'s backward runtime walk (from
`now`). One constant, so the two can never disagree about how far a schedule
may need to look to find (or fail to find) a match — spans every possible
leap-day alignment (e.g. "0 0 29 2 *", Feb 29 only)."""


def _parse_field(raw: str, lo: int, hi: int, normalize=lambda v: v) -> frozenset[int]:
    """Parse one cron field: a comma-separated list of a literal number, `*`,
    a range (`a-b`), or either stepped by `/n`. Raises `ValueError` naming the
    field on anything malformed or out of `[lo, hi]`."""
    values: set[int] = set()
    for item in raw.split(","):
        base, has_step, step_text = item.partition("/")
        if has_step:
            try:
                step = int(step_text)
            except ValueError:
                raise ValueError(f"malformed step in cron field {raw!r}") from None
        else:
            step = 1
        if step <= 0:
            raise ValueError(f"step must be positive in cron field {raw!r}")

        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            left, _, right = base.partition("-")
            try:
                start, end = int(left), int(right)
            except ValueError:
                raise ValueError(f"malformed range in cron field {raw!r}") from None
        else:
            try:
                start = end = int(base)
            except ValueError:
                raise ValueError(f"malformed value in cron field {raw!r}") from None

        if not (lo <= start <= hi and lo <= end <= hi and start <= end):
            raise ValueError(
                f"value out of range in cron field {raw!r}: expected {lo}-{hi}"
            )
        values.update(normalize(v) for v in range(start, end + 1, step))
    return frozenset(values)


def _day_matches(day: date, schedule: "CronSchedule") -> bool:
    """The one place the POSIX day-of-month/day-of-week OR-rule lives, shared
    by `parse_cron`'s existence check and `occurrence_at_or_before`'s runtime
    walk: a day matches if it satisfies *either* restricted field when both
    are restricted, but a plain AND when at most one is."""
    if day.month not in schedule.months:
        return False
    dom_ok = schedule.dom_is_star or day.day in schedule.doms
    # date.weekday(): Mon=0..Sun=6 -> POSIX Sun=0..Sat=6 via (+1) % 7.
    dow_ok = schedule.dow_is_star or (day.weekday() + 1) % 7 in schedule.dows
    if schedule.dom_is_star and schedule.dow_is_star:
        return True
    if schedule.dom_is_star or schedule.dow_is_star:
        return dom_ok and dow_ok
    return dom_ok or dow_ok


@dataclass(frozen=True)
class CronSchedule:
    """A parsed, validated standard 5-field cron expression (minute hour
    day-of-month month day-of-week). Not persisted anywhere — rebuilt from the
    JSON string on every `harness run` / `FilesystemProcessAdmin.write`
    validation, exactly like `parse_interval`'s `float` result. `dom_is_star`/
    `dow_is_star` are `True` only when that field's entire raw text was
    exactly `"*"` — the narrow condition that decides whether the field
    participates in the OR-rule (see `_day_matches`)."""

    minutes: frozenset[int]
    hours: frozenset[int]
    doms: frozenset[int]
    dom_is_star: bool
    months: frozenset[int]
    dows: frozenset[int]
    dow_is_star: bool

    def occurrence_at_or_before(self, now: str) -> str:
        """The most recent minute-aligned UTC timestamp <= `now` matching all
        five fields. Pure function of `now` — no wall-clock reads. Walks
        backward day by day (bounded by `_LOOKBACK_DAYS`), so the common case
        (any schedule firing at least weekly) resolves in single-digit
        iterations; only a schedule matching no day in the window would walk
        the full cap, and that schedule was already rejected by `parse_cron`."""
        moment = datetime.fromisoformat(now.replace("Z", "+00:00")).replace(
            second=0, microsecond=0
        )
        today = moment.date()
        day = today
        for _ in range(_LOOKBACK_DAYS):
            if _day_matches(day, self):
                candidates = [
                    (hour, minute)
                    for hour in self.hours
                    for minute in self.minutes
                    if day < today or (hour, minute) <= (moment.hour, moment.minute)
                ]
                if candidates:
                    hour, minute = max(candidates)
                    return f"{day.isoformat()}T{hour:02d}:{minute:02d}:00Z"
            day -= timedelta(days=1)
        # Unreachable for any CronSchedule that passed parse_cron's validation
        # (its forward scan proves a match exists inside this same window).
        raise RuntimeError(
            f"no cron occurrence found within {_LOOKBACK_DAYS} days before {now!r}"
        )


def parse_cron(text: str) -> CronSchedule:
    """Validate a standard 5-field cron expression and return a `CronSchedule`.

    Supported syntax per field: a literal number, `*`, a list (`a,b,c`), a
    range (`a-b`), a step (`*/n` or `a-b/n`). No named months/days, no 6-field
    seconds. Day-of-week is `0-7`, both `0` and `7` meaning Sunday (POSIX
    convention), `1` = Monday. Raises `ValueError` on a malformed field, an
    out-of-range value, the wrong field count, or a syntactically valid
    schedule that can **never occur** (e.g. day 31 in February) — the latter
    checked by scanning forward from a fixed reference date across the same
    `_LOOKBACK_DAYS` window the runtime walk uses, so a mistyped schedule
    fails fast at load time instead of silently never firing.
    """
    fields = text.split()
    if len(fields) != 5:
        raise ValueError(
            f"cron expression must have exactly 5 fields (minute hour dom month "
            f"dow), got {len(fields)}: {text!r}"
        )
    minute_text, hour_text, dom_text, month_text, dow_text = fields

    schedule = CronSchedule(
        minutes=_parse_field(minute_text, 0, 59),
        hours=_parse_field(hour_text, 0, 23),
        doms=_parse_field(dom_text, 1, 31),
        dom_is_star=(dom_text == "*"),
        months=_parse_field(month_text, 1, 12),
        dows=_parse_field(dow_text, 0, 7, normalize=lambda v: 0 if v == 7 else v),
        dow_is_star=(dow_text == "*"),
    )

    # Impossible-schedule detection: a fixed, arbitrary reference date (not a
    # clock read — chosen only because it spans a leap year), scanned forward.
    reference = date(2028, 1, 1)
    day = reference
    for _ in range(_LOOKBACK_DAYS):
        if _day_matches(day, schedule):
            return schedule
        day += timedelta(days=1)
    raise ValueError(f"cron expression {text!r} can never occur")
