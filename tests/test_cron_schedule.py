"""`parse_cron`/`CronSchedule`: a standard 5-field cron parser and its
occurrence function, the cron twin of `parse_interval`.

Every case is a fixed `now`/reference string in, a fixed occurrence string or
`ValueError` out — no wall-clock reads, no sleeps, matching this project's
`FakeClock`-testable stance for every other port.
"""

from __future__ import annotations

import pytest

from harness.ports.triggers import parse_cron


def test_weekly_monday_occurrence_from_a_tuesday() -> None:
    schedule = parse_cron("0 6 * * 1")

    # 2026-07-21 is a Tuesday; the preceding Monday 06:00Z is 2026-07-20.
    assert schedule.occurrence_at_or_before("2026-07-21T10:00:00Z") == "2026-07-20T06:00:00Z"


def test_occurrence_exactly_at_the_scheduled_minute() -> None:
    schedule = parse_cron("0 6 * * 1")

    assert schedule.occurrence_at_or_before("2026-07-20T06:00:00Z") == "2026-07-20T06:00:00Z"


def test_occurrence_one_minute_before_falls_back_to_the_prior_week() -> None:
    schedule = parse_cron("0 6 * * 1")

    assert schedule.occurrence_at_or_before("2026-07-20T05:59:00Z") == "2026-07-13T06:00:00Z"


def test_stepped_minute_field() -> None:
    schedule = parse_cron("*/15 * * * *")

    assert schedule.occurrence_at_or_before("2026-07-21T10:07:00Z") == "2026-07-21T10:00:00Z"
    assert schedule.occurrence_at_or_before("2026-07-21T10:15:00Z") == "2026-07-21T10:15:00Z"


def test_hour_range_and_weekday_list() -> None:
    schedule = parse_cron("0 9-17 * * 1-5")

    # 2026-07-25 is a Saturday; the last matching moment is Friday 17:00Z.
    assert schedule.occurrence_at_or_before("2026-07-25T20:00:00Z") == "2026-07-24T17:00:00Z"


def test_seconds_within_the_same_minute_do_not_change_the_occurrence() -> None:
    schedule = parse_cron("0 6 * * 1")

    assert (
        schedule.occurrence_at_or_before("2026-07-20T06:00:30Z")
        == schedule.occurrence_at_or_before("2026-07-20T06:00:00Z")
    )


def test_dom_and_dow_both_restricted_is_an_or_not_an_and() -> None:
    # POSIX OR-rule: day 15 OR a Monday. 2026-08-03 is a Monday but not the
    # 15th; it must still match.
    schedule = parse_cron("0 0 15 * 1")

    assert schedule.occurrence_at_or_before("2026-08-03T12:00:00Z") == "2026-08-03T00:00:00Z"


def test_dom_star_dow_restricted_is_a_plain_constraint() -> None:
    # dom is "*" (unrestricted) so only the dow constraint applies: every Monday.
    schedule = parse_cron("0 0 * * 1")

    assert schedule.occurrence_at_or_before("2026-08-04T12:00:00Z") == "2026-08-03T00:00:00Z"


def test_sunday_as_0_and_7_are_equivalent() -> None:
    zero = parse_cron("0 0 * * 0")
    seven = parse_cron("0 0 * * 7")

    now = "2026-07-23T12:00:00Z"  # a Thursday
    assert zero.occurrence_at_or_before(now) == seven.occurrence_at_or_before(now)


# --- impossible-schedule / malformed-syntax rejection -----------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "* * * *",  # only 4 fields
        "* * * * * *",  # 6 fields
        "60 * * * *",  # minute out of range
        "0 24 * * *",  # hour out of range
        "0 0 32 * *",  # day-of-month out of range
        "0 0 * 13 *",  # month out of range
        "0 0 * * 8",  # day-of-week out of range
        "0 6 31 2 *",  # Feb 31st is never valid, and dow="*" gives no rescue
        "x 0 * * *",  # non-numeric
        "0 0 * */0 *",  # zero step
    ],
)
def test_malformed_or_impossible_expressions_raise(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_cron(bad)


def test_feb_29_only_schedule_is_accepted() -> None:
    # A quadrennial schedule must not be rejected by too-short a look-back
    # window — it's the canary for the shared `_LOOKBACK_DAYS` constant.
    schedule = parse_cron("0 0 29 2 *")

    assert schedule is not None


def test_occurrence_at_or_before_finds_feb_29_from_a_non_leap_year_now() -> None:
    schedule = parse_cron("0 0 29 2 *")

    # 2026 is not a leap year; the most recent Feb 29 is 2024.
    assert schedule.occurrence_at_or_before("2026-07-23T00:00:00Z") == "2024-02-29T00:00:00Z"
