"""Tests for the business calendar (BLUEPRINT.md §3.3, §3.4.3)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from tests.conftest import FakeCalendarProvider

from freshcal.core.calendar import MAX_ROLL_DAYS, BusinessCalendar
from freshcal.core.errors import CalendarError, Issue
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    FreshnessTarget,
    HolidayCalendarRef,
    Origin,
    RawObservation,
    SourceRule,
    Weekday,
)
from freshcal.core.verdict import evaluate

XECB = HolidayCalendarRef("financial", "XECB")
DE = HolidayCalendarRef("country", "DE")
WEEKEND = frozenset({Weekday.SAT, Weekday.SUN})

# 2026-09-25 is a Friday; 09-28 Monday; 10-03 Saturday; 10-04 Sunday.
FRIDAY = date(2026, 9, 25)
MONDAY = date(2026, 9, 28)
SATURDAY = date(2026, 9, 26)
SUNDAY = date(2026, 9, 27)


def calendar(
    spec: CalendarSpec | None = None,
    holidays: dict[tuple[str, int], dict[date, str]] | None = None,
    *,
    source_id: str | None = None,
    start_year: int = 1900,
    end_year: int = 2100,
) -> BusinessCalendar:
    provider = FakeCalendarProvider(holidays or {}, start_year=start_year, end_year=end_year)
    return BusinessCalendar(spec or CalendarSpec(), provider, source_id=source_id)


def test_u_cal_01_default_weekend() -> None:
    cal = calendar()
    assert cal.is_business_day(FRIDAY)
    assert cal.is_business_day(MONDAY)
    assert not cal.is_business_day(SATURDAY)
    assert not cal.is_business_day(SUNDAY)


def test_u_cal_02_empty_weekend() -> None:
    cal = calendar(CalendarSpec(weekend=frozenset()))
    assert all(cal.is_business_day(day) for day in (SATURDAY, SUNDAY, MONDAY, FRIDAY))
    assert cal.non_business_reason(SATURDAY) is None


def test_u_cal_03_union_of_two_calendars() -> None:
    holiday = date(2026, 5, 1)
    cal = calendar(
        CalendarSpec(holiday_calendars=(DE, XECB)),
        {
            ("country DE", 2026): {holiday: "Labour Day"},
            ("financial XECB", 2026): {date(2026, 12, 25): "Christmas Day"},
        },
    )
    assert not cal.is_business_day(holiday)
    assert not cal.is_business_day(date(2026, 12, 25))
    assert cal.is_business_day(date(2026, 12, 24))
    assert cal.non_business_reason(holiday) == "holiday: Labour Day (country DE)"
    assert cal.non_business_reason(date(2026, 12, 25)) == (
        "holiday: Christmas Day (financial XECB)"
    )


def test_u_cal_04_extra_working_day_beats_weekend_and_holiday() -> None:
    vacation = date(2026, 5, 1)
    cal = calendar(
        CalendarSpec(
            holiday_calendars=(DE,),
            extra_working_days=frozenset({SATURDAY, vacation}),
        ),
        {("country DE", 2026): {vacation: "Labour Day"}},
    )
    assert cal.is_business_day(SATURDAY)
    assert cal.non_business_reason(SATURDAY) is None
    assert cal.is_business_day(vacation)
    assert not cal.is_business_day(SUNDAY)


def test_u_cal_05_extra_non_working_day() -> None:
    wednesday = date(2026, 9, 30)
    cal = calendar(CalendarSpec(extra_non_working_days=frozenset({wednesday})))
    assert not cal.is_business_day(wednesday)
    assert cal.non_business_reason(wednesday) == "override: non-working day"
    assert cal.non_business_reason(SUNDAY) == "weekend"


def test_u_cal_06_roll_over_weekend_and_holiday() -> None:
    # A Friday before a weekend and a Monday holiday rolls to Tuesday.
    monday_holiday = MONDAY
    cal = calendar(
        CalendarSpec(holiday_calendars=(DE,)),
        {("country DE", 2026): {monday_holiday: "Bank holiday"}},
    )
    assert cal.roll(FRIDAY, 1) == date(2026, 9, 29)
    assert cal.roll(date(2026, 9, 29), -1) == FRIDAY
    assert cal.roll(FRIDAY, -1) == date(2026, 9, 24)


def test_u_cal_07_roll_farther_than_the_limit_is_e407() -> None:
    # A run of non-working days longer than MAX_ROLL_DAYS in both directions.
    blocked = frozenset(date(2026, 8, 1) + timedelta(days=offset) for offset in range(122))
    cal = calendar(CalendarSpec(extra_non_working_days=blocked))

    with pytest.raises(CalendarError) as excinfo:
        cal.roll(date(2026, 9, 1), 1)
    issue = excinfo.value.issue
    assert issue.code == "E407"
    assert issue.message == f"no business day within {MAX_ROLL_DAYS} days after 2026-09-01"

    with pytest.raises(CalendarError) as excinfo:
        cal.roll(date(2026, 9, 30), -1)
    assert excinfo.value.issue.message == (
        f"no business day within {MAX_ROLL_DAYS} days before 2026-09-30"
    )


def test_u_cal_08_non_business_reason_strings() -> None:
    good_friday = date(2026, 4, 3)
    cal = calendar(
        CalendarSpec(
            holiday_calendars=(XECB,),
            extra_non_working_days=frozenset({date(2026, 6, 12)}),
        ),
        {("financial XECB", 2026): {good_friday: "Good Friday"}},
    )
    assert cal.non_business_reason(good_friday) == "holiday: Good Friday (financial XECB)"
    assert cal.non_business_reason(date(2026, 6, 12)) == "override: non-working day"
    assert cal.non_business_reason(SUNDAY) == "weekend"
    assert cal.non_business_reason(FRIDAY) is None


def test_u_cal_09_provider_e405_propagates() -> None:
    provider = FakeCalendarProvider({}, start_year=1999, end_year=2100)
    cal = BusinessCalendar(CalendarSpec(holiday_calendars=(XECB,)), provider)
    with pytest.raises(CalendarError) as excinfo:
        cal.is_business_day(date(1998, 12, 31))
    assert excinfo.value.issue.code == "E405"
    assert excinfo.value.issue.message == (
        "calendar financial XECB has no holiday data for 1998 (supported 1999-2100)"
    )


def test_u_cal_10_valid_until_and_w005_tracking() -> None:
    cal = calendar(
        CalendarSpec(valid_until=date(2026, 12, 31), name="cn_workdays"),
        source_id="cn.daily_sales",
    )
    cal.check_valid_at(date(2026, 12, 31))
    with pytest.raises(CalendarError) as excinfo:
        cal.check_valid_at(date(2027, 1, 4))
    issue = excinfo.value.issue
    assert issue.code == "E408"
    assert issue.message == (
        "calendar cn_workdays is valid until 2026-12-31, but the evaluation date is "
        "2027-01-04; review its holidays and overrides for the next period and extend "
        "valid_until"
    )

    # A lookup past valid_until does not raise, but is remembered for W005.
    assert cal.max_date_looked_up is None
    assert cal.consulted_past_valid_until() is None
    cal.is_business_day(date(2027, 1, 4))
    assert cal.max_date_looked_up == date(2027, 1, 4)
    assert cal.consulted_past_valid_until() == date(2027, 1, 4)

    # A calendar without valid_until never reports a lookup.
    open_calendar = calendar(source_id="x")
    open_calendar.is_business_day(date(2099, 1, 4))
    assert open_calendar.consulted_past_valid_until() is None


def test_u_cal_11_w005_notice_window_starts_34_days_before_valid_until() -> None:
    """SEM-04: the notice window is explicit; the first W005 is 2026-11-28.

    ``review/v0.1.0/A-semantics/w005_timing.py`` evaluates this rule daily at 08:00
    Asia/Shanghai: with ``valid_until: 2026-12-31`` and a 34-day notice window
    (``CHUNK + DATE_PADDING_DAYS``) the window first reaches past the valid date on
    2026-11-28, and W005 stays on every day through 2026-12-31 — 34 of the 42 days
    evaluated. The window is declared by ``note_horizon``, so ``check``, ``next`` and
    ``validate`` all give this answer however far their searches happen to look.
    """
    shanghai = ZoneInfo("Asia/Shanghai")
    rule = SourceRule(
        source_id="cn.daily_sales",
        origin=Origin.CONFIG,
        schedule=BusinessDaysSchedule(at=time(15, 0), timezone=shanghai),
        calendar=CalendarSpec(name="cn_workdays", valid_until=date(2026, 12, 31)),
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation="raw.cn", loaded_at_field="_loaded_at"),
        observed_timezone=shanghai,
    )
    warnings_by_day: list[tuple[date, tuple[Issue, ...]]] = []
    day = date(2026, 11, 20)
    while day <= date(2026, 12, 31):
        now = datetime.combine(day, time(8, 0), tzinfo=shanghai)
        observed = datetime.combine(day - timedelta(days=1), time(15, 5))
        result = evaluate(rule, RawObservation(observed), now, FakeCalendarProvider())
        warnings_by_day.append((day, result.warnings))
        day += timedelta(days=1)

    first_w005 = next(
        day for day, warnings in warnings_by_day if any(issue.code == "W005" for issue in warnings)
    )
    assert first_w005 == date(2026, 11, 28)
    assert all(
        any(issue.code == "W005" for issue in warnings)
        for day, warnings in warnings_by_day
        if day >= date(2026, 11, 28)
    )
    assert all(
        all(issue.code != "W005" for issue in warnings)
        for day, warnings in warnings_by_day
        if day < date(2026, 11, 28)
    )
    first_day = next(warnings for day, warnings in warnings_by_day if day == first_w005)
    assert first_day[0].message == (
        "calendar cn_workdays was consulted for 2027-01-01, after its valid_until "
        "2026-12-31; the next expected arrival may be wrong"
    )
    assert first_day[0].location == "cn.daily_sales"


def test_weekend_and_override_decisions_are_recorded_as_lookups() -> None:
    """The suspected lookup gap: weekend and override answers short-circuit the provider.

    ``is_business_day`` returns early for an explicit working day, an explicit
    non-working day and a weekend day, so those decisions used to leave no trace in
    ``max_date_looked_up``. Every predicate answer now records its date, which keeps the
    W005 notice honest even for a decision a search makes beyond the declared window.
    The declared window (``note_horizon``) is what makes the *answer* independent of the
    search shape; this test pins both halves.
    """
    cal = calendar(
        CalendarSpec(
            name="cn_workdays",
            valid_until=date(2026, 12, 31),
            extra_working_days=frozenset({SATURDAY}),
            extra_non_working_days=frozenset({date(2027, 1, 5)}),
        ),
        source_id="cn.daily_sales",
    )
    # An explicit working day beats the weekend, and the decision is recorded.
    assert cal.is_business_day(SATURDAY)
    assert cal.max_date_looked_up == SATURDAY
    assert cal.consulted_past_valid_until() is None

    # A weekend day and an explicit non-working day past valid_until are decisions too.
    sunday = date(2027, 1, 3)
    assert not cal.is_business_day(sunday)
    assert cal.max_date_looked_up == sunday
    override = date(2027, 1, 5)
    assert not cal.is_business_day(override)
    assert cal.max_date_looked_up == override
    assert cal.consulted_past_valid_until() == override

    # A declared horizon is recorded even when no predicate asked about it — this is
    # what makes W005 independent of how far a search happens to look.
    declared = calendar(CalendarSpec(name="cn_workdays", valid_until=date(2026, 12, 31)))
    declared.note_horizon(date(2027, 1, 2))  # a Saturday
    assert declared.max_date_looked_up == date(2027, 1, 2)
    assert declared.consulted_past_valid_until() == date(2027, 1, 2)


def test_labels_for_inline_and_named_calendars() -> None:
    assert calendar(CalendarSpec(name="target")).label == "target"
    assert calendar(source_id="ecb.fx_rates").label == "of ecb.fx_rates"
    assert calendar().label == "inline"


def test_c2_roll_reaches_exactly_max_roll_days() -> None:
    """Kills mutant C2: a business day exactly ``MAX_ROLL_DAYS`` away is reachable.

    With the first 30 days blocked the next business day is 31 days out, and §3.4.3
    allows the roll; the ``range(1, MAX_ROLL_DAYS)`` mutant stops one day short and
    raises E407. With 31 days blocked (32 away) E407 is the correct answer, so the test
    pins both sides of the boundary.
    """
    start = date(2026, 9, 1)
    blocked_30 = frozenset(start + timedelta(days=offset) for offset in range(1, 31))
    cal = calendar(CalendarSpec(extra_non_working_days=blocked_30))
    assert cal.roll(start, 1) == start + timedelta(days=MAX_ROLL_DAYS)

    blocked_31 = frozenset(start + timedelta(days=offset) for offset in range(1, 32))
    cal = calendar(CalendarSpec(extra_non_working_days=blocked_31))
    with pytest.raises(CalendarError) as excinfo:
        cal.roll(start, 1)
    assert excinfo.value.issue.code == "E407"


def test_c4_lookup_equal_to_valid_until_is_not_w005() -> None:
    """Kills mutant C4: ``max_date_looked_up == valid_until`` is still inside the calendar.

    The last valid day is part of the calendar; only a lookup strictly after
    ``valid_until`` is consulted past it. The ``>=`` mutant reports W005 one day early,
    so every rule would warn on its calendar's last valid day.
    """
    cal = calendar(
        CalendarSpec(valid_until=date(2026, 12, 31), name="cn_workdays"),
        source_id="cn.daily_sales",
    )
    cal.is_business_day(date(2026, 12, 31))
    assert cal.max_date_looked_up == date(2026, 12, 31)
    assert cal.consulted_past_valid_until() is None

    cal.is_business_day(date(2027, 1, 1))
    assert cal.consulted_past_valid_until() == date(2027, 1, 1)


def test_holiday_lookup_caches_by_year_per_ref() -> None:
    # Two refs that both list the same date: the first one wins in the merged name.
    day = date(2026, 5, 1)
    cal = calendar(
        CalendarSpec(holiday_calendars=(DE, XECB)),
        {
            ("country DE", 2026): {day: "Labour Day"},
            ("financial XECB", 2026): {day: "XECB closing day"},
        },
    )
    assert cal.non_business_reason(day) == "holiday: Labour Day (country DE)"
    assert cal.non_business_reason(date(2027, 5, 1)) == "weekend"
    assert ZoneInfo("UTC") is not None
