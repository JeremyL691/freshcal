"""Hypothesis strategies for the schedule and verdict property tests (§9.3).

The time-zone list is the one the blueprint names, including the ones whose DST
transitions are unusual (a 30-minute shift for ``Australia/Lord_Howe``, a 45-minute
offset for ``Asia/Kathmandu``, midnight for ``America/Santiago``). Schedules come from
a deliberately small grammar so that failures are explainable, and calendars come from
``FakeCalendarProvider`` so a test states its own holidays.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import hypothesis.strategies as st
from tests.conftest import FakeCalendarProvider

from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    CronSchedule,
    FreshnessTarget,
    HolidayCalendarRef,
    MonthlyBusinessDaySchedule,
    NonBusinessDayPolicy,
    Origin,
    SourceRule,
    Weekday,
)

TIMEZONES: tuple[ZoneInfo, ...] = (
    ZoneInfo("UTC"),
    ZoneInfo("Europe/Berlin"),
    ZoneInfo("America/New_York"),
    ZoneInfo("America/Santiago"),
    ZoneInfo("Australia/Lord_Howe"),
    ZoneInfo("Asia/Kathmandu"),
    ZoneInfo("Pacific/Chatham"),
    ZoneInfo("Asia/Shanghai"),
    ZoneInfo("Pacific/Kiritimati"),
)

HOLIDAY_REFS: tuple[HolidayCalendarRef, ...] = (
    HolidayCalendarRef("financial", "XECB"),
    HolidayCalendarRef("country", "DE"),
    HolidayCalendarRef("country", "US"),
)

START_INSTANT = datetime(2001, 1, 1, tzinfo=UTC)
END_INSTANT = datetime(2090, 12, 31, 23, 59, tzinfo=UTC)

CRON_MINUTES = ("0", "15", "30", "45", "*/30")
CRON_HOURS = ("0", "6", "12", "18", "*/6")
CRON_DAYS_OF_MONTH = ("*", "1", "15", "L", "29", "30", "31")
CRON_DAYS_OF_WEEK = ("*", "1-5", "0,6")
SUB_HOURLY_MINUTES = ("*/30",)


def instants() -> st.SearchStrategy[datetime]:
    """Aware UTC instants between 2001-01-01 and 2090-12-31."""
    return st.datetimes(
        min_value=START_INSTANT.replace(tzinfo=None),
        max_value=END_INSTANT.replace(tzinfo=None),
    ).map(lambda naive: naive.replace(tzinfo=UTC))


def timezones() -> st.SearchStrategy[ZoneInfo]:
    return st.sampled_from(TIMEZONES)


def cron_schedules(*, sub_hourly: bool = True) -> st.SearchStrategy[CronSchedule]:
    """Cron schedules from the small grammar of §9.3."""
    minutes = (
        CRON_MINUTES
        if sub_hourly
        else tuple(value for value in CRON_MINUTES if value not in SUB_HOURLY_MINUTES)
    )
    return st.builds(
        _cron_schedule,
        st.sampled_from(minutes),
        st.sampled_from(CRON_HOURS),
        st.sampled_from(CRON_DAYS_OF_MONTH),
        st.sampled_from(CRON_DAYS_OF_WEEK),
        timezones(),
        st.sampled_from(list(NonBusinessDayPolicy)),
    )


def _cron_schedule(
    minute: str,
    hour: str,
    day_of_month: str,
    day_of_week: str,
    timezone: ZoneInfo,
    policy: NonBusinessDayPolicy,
) -> CronSchedule:
    return CronSchedule(
        expression=f"{minute} {hour} {day_of_month} * {day_of_week}",
        timezone=timezone,
        on_non_business_day=policy,
    )


def business_day_schedules() -> st.SearchStrategy[BusinessDaysSchedule]:
    return st.builds(BusinessDaysSchedule, st.times(), timezones())


def monthly_schedules() -> st.SearchStrategy[MonthlyBusinessDaySchedule]:
    numbers = [value for value in range(-23, 24) if value != 0]
    return st.builds(MonthlyBusinessDaySchedule, st.sampled_from(numbers), st.times(), timezones())


def schedules(
    *, sub_hourly: bool = True
) -> st.SearchStrategy[CronSchedule | BusinessDaysSchedule | MonthlyBusinessDaySchedule]:
    options: list[st.SearchStrategy[object]] = [
        cron_schedules(sub_hourly=sub_hourly),
        business_day_schedules(),
        monthly_schedules(),
    ]
    return st.one_of(options)  # type: ignore[return-value]


def calendars() -> st.SearchStrategy[tuple[CalendarSpec, FakeCalendarProvider]]:
    """A calendar spec plus a provider whose holidays are drawn per year."""

    @st.composite
    def build(draw: st.DrawFn) -> tuple[CalendarSpec, FakeCalendarProvider]:
        weekend = frozenset(draw(st.sets(st.sampled_from(list(Weekday)), max_size=6)))
        ref = draw(st.sampled_from(HOLIDAY_REFS))
        holiday_dates: dict[tuple[str, int], dict[date, str]] = {}
        for year in range(2024, 2031):
            days = draw(
                st.sets(
                    st.dates(min_value=date(year, 1, 1), max_value=date(year, 12, 31)),
                    max_size=4,
                )
            )
            holiday_dates[(ref.label(), year)] = dict.fromkeys(days, "Test holiday")
        working = frozenset(
            draw(
                st.sets(
                    st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31)), max_size=3
                )
            )
        )
        non_working = (
            frozenset(
                draw(
                    st.sets(
                        st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31)),
                        max_size=3,
                    )
                )
            )
            - working
        )
        spec = CalendarSpec(
            weekend=weekend,
            holiday_calendars=(ref,),
            extra_working_days=working,
            extra_non_working_days=non_working,
        )
        return spec, FakeCalendarProvider(holiday_dates)

    return build()


def rules(
    *, sub_hourly: bool = True
) -> st.SearchStrategy[tuple[SourceRule, CalendarSpec, FakeCalendarProvider]]:
    """A rule together with the calendar objects its schedule must be evaluated with."""

    @st.composite
    def build(draw: st.DrawFn) -> tuple[SourceRule, CalendarSpec, FakeCalendarProvider]:
        schedule = draw(schedules(sub_hourly=sub_hourly))
        spec, provider = draw(calendars())
        active_from = draw(
            st.one_of(
                st.none(),
                st.dates(min_value=date(2001, 1, 1), max_value=date(2089, 12, 31)),
            )
        )
        rule = SourceRule(
            source_id="property.source",
            origin=Origin.CONFIG,
            schedule=schedule,
            calendar=spec,
            grace=timedelta(minutes=draw(st.integers(min_value=0, max_value=1440))),
            target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
            active_from=active_from,
        )
        return rule, spec, provider

    return build()


def windows(max_days: int = 45) -> st.SearchStrategy[tuple[datetime, datetime]]:
    """``(start, end)`` pairs whose span is at most ``max_days``."""

    @st.composite
    def build(draw: st.DrawFn) -> tuple[datetime, datetime]:
        start = draw(instants())
        length = draw(
            st.timedeltas(
                min_value=timedelta(hours=1),
                max_value=timedelta(days=max_days),
            )
        )
        return start, start + length

    return build()
