"""Tests for release generation: cron schedules (BLUEPRINT.md §3.4.1-§3.4.3)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from tests.conftest import FakeCalendarProvider

from freshcal.core.calendar import BusinessCalendar
from freshcal.core.errors import ConfigError
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
)
from freshcal.core.schedule import (
    MAX_ROLL_DAYS,
    next_release_after,
    previous_release_at_or_before,
    releases_between,
)
from freshcal.core.timeutil import resolve_local

BERLIN = ZoneInfo("Europe/Berlin")
UTC_ZONE = ZoneInfo("UTC")
DE = HolidayCalendarRef("country", "DE")


def make_rule(
    schedule: CronSchedule,
    *,
    calendar: CalendarSpec | None = None,
    active_from: date | None = None,
    source_id: str = "vendor.daily",
) -> SourceRule:
    return SourceRule(
        source_id=source_id,
        origin=Origin.CONFIG,
        schedule=schedule,
        calendar=calendar or CalendarSpec(),
        grace=timedelta(hours=1),
        target=FreshnessTarget(relation="raw.daily", loaded_at_field="_loaded_at"),
        active_from=active_from,
    )


def make_calendar(
    spec: CalendarSpec | None = None,
    holidays: dict[tuple[str, int], dict[date, str]] | None = None,
) -> BusinessCalendar:
    return BusinessCalendar(spec or CalendarSpec(), FakeCalendarProvider(holidays or {}))


def test_u_sch_01_weekday_cron_releases() -> None:
    """`0 16 * * 1-5` Berlin over two weeks: exactly the ten weekday instants."""
    rule = make_rule(CronSchedule("0 16 * * 1-5", BERLIN))
    start = datetime(2026, 9, 21, 0, 0, tzinfo=UTC)  # Monday
    end = datetime(2026, 10, 2, 23, 59, 59, tzinfo=UTC)
    releases = releases_between(rule, start, end, make_calendar())

    assert len(releases) == 10
    assert [release.local.strftime("%a %Y-%m-%d %H:%M") for release in releases[:2]] == [
        "Mon 2026-09-21 16:00",
        "Tue 2026-09-22 16:00",
    ]
    assert releases[-1].local.strftime("%a %Y-%m-%d %H:%M") == "Fri 2026-10-02 16:00"
    # CEST: 16:00 local is 14:00Z, and the first release is Monday 2026-09-21.
    assert releases[0].instant == datetime(2026, 9, 21, 14, 0, tzinfo=UTC)
    assert all(release.instant.hour == 14 for release in releases)
    assert all(release.dst == "normal" for release in releases)
    assert all(release.adjusted_from is None for release in releases)
    assert all(not release.clamped for release in releases)


def test_u_sch_05_skip_drops_non_business_days() -> None:
    easter_monday = date(2026, 4, 6)
    calendar = make_calendar(
        CalendarSpec(holiday_calendars=(DE,)),
        {("country DE", 2026): {date(2026, 4, 3): "Good Friday", easter_monday: "Easter Monday"}},
    )
    rule = make_rule(
        CronSchedule("0 9 * * *", BERLIN, NonBusinessDayPolicy.SKIP),
        calendar=CalendarSpec(holiday_calendars=(DE,)),
    )
    releases = releases_between(
        rule,
        datetime(2026, 4, 2, 0, 0, tzinfo=UTC),
        datetime(2026, 4, 7, 23, 59, 59, tzinfo=UTC),
        calendar,
    )
    # `skip` drops every non-business day: the two holidays and the weekend.
    assert [release.local.strftime("%a %Y-%m-%d") for release in releases] == [
        "Thu 2026-04-02",
        "Tue 2026-04-07",
    ]


def test_u_sch_06_following_collision_dedupe() -> None:
    """Saturday, Sunday, and Monday all roll to Monday: one release, Saturday's date."""
    rule = make_rule(CronSchedule("0 6 * * *", BERLIN, NonBusinessDayPolicy.FOLLOWING))
    releases = releases_between(
        rule,
        datetime(2026, 9, 25, 0, 0, tzinfo=UTC),  # Friday
        datetime(2026, 9, 29, 23, 59, 59, tzinfo=UTC),  # Tuesday
        make_calendar(),
    )
    saturday = date(2026, 9, 26)
    assert [release.local.strftime("%a %Y-%m-%d %H:%M") for release in releases] == [
        "Fri 2026-09-25 06:00",
        "Mon 2026-09-28 06:00",
        "Tue 2026-09-29 06:00",
    ]
    monday = releases[1]
    assert monday.instant == resolve_local(datetime(2026, 9, 28, 6, 0), BERLIN)
    assert monday.adjusted_from == saturday


def test_u_sch_07_preceding_across_a_month_boundary() -> None:
    """15 November 2026 is a Sunday, so `0 9 15 * *` with `preceding` moves to Friday 13."""
    rule = make_rule(CronSchedule("0 9 15 * *", BERLIN, NonBusinessDayPolicy.PRECEDING))
    releases = releases_between(
        rule,
        datetime(2026, 11, 1, 0, 0, tzinfo=UTC),
        datetime(2026, 11, 30, 23, 59, 59, tzinfo=UTC),
        make_calendar(),
    )
    assert [
        (release.local.strftime("%a %Y-%m-%d %H:%M"), release.adjusted_from) for release in releases
    ] == [("Fri 2026-11-13 09:00", date(2026, 11, 15))]


def test_u_sch_08_active_from_floor_including_a_missing_local_midnight() -> None:
    """Santiago's local midnight does not exist on 2026-09-06 (fold=0 shifts it to 01:00)."""
    santiago = ZoneInfo("America/Santiago")
    midnight = resolve_local(datetime(2026, 9, 6, 0, 0), santiago)
    assert midnight == datetime(2026, 9, 6, 4, 0, tzinfo=UTC)  # 01:00 local after the gap

    rule = make_rule(
        CronSchedule("0 * * * *", santiago),
        active_from=date(2026, 9, 6),
    )
    releases = releases_between(
        rule,
        datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 6, 6, 0, tzinfo=UTC),
        make_calendar(),
    )
    assert releases
    assert releases[0].instant == midnight
    assert all(release.instant >= midnight for release in releases)


def test_u_sch_11_sub_hourly_cron_across_dst_transitions() -> None:
    rule = make_rule(CronSchedule("*/30 * * * *", BERLIN))

    def local_day(day: date) -> tuple[datetime, datetime]:
        start = resolve_local(datetime.combine(day, time.min), BERLIN)
        end = resolve_local(datetime.combine(day + timedelta(days=1), time.min), BERLIN)
        return start, end - timedelta(microseconds=1)

    spring = releases_between(rule, *local_day(date(2026, 3, 29)), make_calendar())
    autumn = releases_between(rule, *local_day(date(2026, 10, 25)), make_calendar())
    assert len(spring) == 46
    assert len(autumn) == 48
    # 02:00 and 02:30 do not exist in Berlin on 2026-03-29; they shift onto 03:00/03:30.
    assert [release.local.strftime("%H:%M") for release in spring][:7] == [
        "00:00",
        "00:30",
        "01:00",
        "01:30",
        "03:00",
        "03:30",
        "04:00",
    ]
    assert [release.dst for release in spring if release.local.strftime("%H:%M") == "03:00"] == [
        "gap"
    ]
    # The fall-back hour occurs once (fold=0), so the count matches a normal day.
    assert [release.local.strftime("%H:%M") for release in autumn][4:8] == [
        "02:00",
        "02:30",
        "03:00",
        "03:30",
    ]
    assert [release.dst for release in autumn if release.local.strftime("%H:%M") == "02:30"] == [
        "ambiguous"
    ]


def test_u_sch_12_never_firing_cron_is_e209() -> None:
    rule = make_rule(CronSchedule("0 0 30 2 *", UTC_ZONE))
    with pytest.raises(ConfigError) as excinfo:
        releases_between(
            rule,
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 12, 31, tzinfo=UTC),
            make_calendar(),
        )
    issue = excinfo.value.issue
    assert issue.code == "E209"
    assert issue.message == (
        "schedule produces no release within 1830 days before or after 2025-12-30"
    )


def test_u_sch_15_window_edges_are_inclusive() -> None:
    rule = make_rule(CronSchedule("0 6 * * *", UTC_ZONE))
    start = datetime(2026, 9, 28, 6, 0, tzinfo=UTC)
    end = datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
    releases = releases_between(rule, start, end, make_calendar())
    assert [release.instant for release in releases] == [
        datetime(2026, 9, 28, 6, 0, tzinfo=UTC),
        datetime(2026, 9, 29, 6, 0, tzinfo=UTC),
        datetime(2026, 9, 30, 6, 0, tzinfo=UTC),
    ]
    assert releases[0].instant == start
    assert releases[-1].instant == end


def test_roll_failure_propagates_e407() -> None:
    """A `following` policy over a year of non-working days fails with E407."""
    every_day = frozenset(date(2026, 1, 1) + timedelta(days=offset) for offset in range(400))
    calendar = make_calendar(CalendarSpec(extra_non_working_days=every_day))
    rule = make_rule(CronSchedule("0 6 * * *", UTC_ZONE, NonBusinessDayPolicy.FOLLOWING))
    with pytest.raises(ConfigError) as excinfo:
        releases_between(
            rule,
            datetime(2026, 3, 1, tzinfo=UTC),
            datetime(2026, 3, 2, tzinfo=UTC),
            calendar,
        )
    assert excinfo.value.issue.code == "E407"
    # The window is padded by MAX_ROLL_DAYS, so the failure is reported for the first
    # padded nominal release rather than for the requested start date.
    assert excinfo.value.issue.message.startswith(
        f"no business day within {MAX_ROLL_DAYS} days after "
    )


def test_windows_are_additive_across_arbitrary_boundaries() -> None:
    rule = make_rule(CronSchedule("0 6 * * *", BERLIN, NonBusinessDayPolicy.FOLLOWING))
    calendar = make_calendar()
    whole = releases_between(
        rule,
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 10, 5, tzinfo=UTC),
        calendar,
    )
    split = releases_between(
        rule, datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 20, tzinfo=UTC), calendar
    ) + releases_between(
        rule,
        datetime(2026, 9, 20, tzinfo=UTC) + timedelta(microseconds=1),
        datetime(2026, 10, 5, tzinfo=UTC),
        calendar,
    )
    assert [release.instant for release in whole] == [release.instant for release in split]


# --------------------------------------------------------------------------
# Business-day schedule kinds and release searches (T-2.4)
# --------------------------------------------------------------------------


def business_days_rule(
    at: time = time(16, 0),
    *,
    timezone: ZoneInfo = BERLIN,
    calendar: CalendarSpec | None = None,
    active_from: date | None = None,
) -> SourceRule:
    return make_rule(
        BusinessDaysSchedule(at=at, timezone=timezone),
        calendar=calendar,
        active_from=active_from,
    )


def monthly_rule(
    business_day: int,
    at: time = time(9, 0),
    *,
    timezone: ZoneInfo = BERLIN,
    calendar: CalendarSpec | None = None,
) -> SourceRule:
    return make_rule(
        MonthlyBusinessDaySchedule(business_day=business_day, at=at, timezone=timezone),
        calendar=calendar,
    )


def test_u_sch_02_business_days_skips_weekends_and_holidays() -> None:
    good_friday = date(2026, 4, 3)
    easter_monday = date(2026, 4, 6)
    spec = CalendarSpec(holiday_calendars=(DE,))
    calendar = make_calendar(
        spec, {("country DE", 2026): {good_friday: "Good Friday", easter_monday: "Easter Monday"}}
    )
    rule = business_days_rule(calendar=spec, active_from=date(2026, 3, 30))
    releases = releases_between(
        rule,
        datetime(2026, 3, 30, tzinfo=UTC_ZONE),
        datetime(2026, 4, 7, 23, 59, 59, tzinfo=UTC_ZONE),
        calendar,
    )
    assert [release.local.strftime("%a %Y-%m-%d %H:%M") for release in releases] == [
        "Mon 2026-03-30 16:00",
        "Tue 2026-03-31 16:00",
        "Wed 2026-04-01 16:00",
        "Thu 2026-04-02 16:00",
        "Tue 2026-04-07 16:00",
    ]
    assert releases[0].instant == datetime(2026, 3, 30, 14, 0, tzinfo=UTC_ZONE)


def test_u_sch_03_monthly_business_day_nth() -> None:
    calendar = make_calendar()
    for index, expected in [
        (1, ["2026-01-01", "2026-02-02", "2026-03-02"]),
        (3, ["2026-01-05", "2026-02-04", "2026-03-04"]),
        (23, ["2026-01-30", "2026-02-27", "2026-03-31"]),
    ]:
        rule = monthly_rule(index)
        releases = releases_between(
            rule,
            datetime(2026, 1, 1, tzinfo=UTC_ZONE),
            datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC_ZONE),
            calendar,
        )
        assert [release.local.date().isoformat() for release in releases] == expected
        assert all(release.local.time() == time(9, 0) for release in releases)
        # February 2026 has 20 business days and March 22, so the 23rd business day is
        # clamped in both months; the 1st and 3rd never are.
        assert [release.clamped for release in releases] == [index == 23] * len(expected)


def test_u_sch_04_monthly_business_day_from_the_end() -> None:
    calendar = make_calendar()
    last = releases_between(
        monthly_rule(-1, at=time(17, 0)),
        datetime(2026, 1, 1, tzinfo=UTC_ZONE),
        datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC_ZONE),
        calendar,
    )
    assert [release.local.date().isoformat() for release in last] == [
        "2026-01-30",
        "2026-02-27",
        "2026-03-31",
    ]
    second_last = releases_between(
        monthly_rule(-2),
        datetime(2026, 1, 1, tzinfo=UTC_ZONE),
        datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC_ZONE),
        calendar,
    )
    assert [release.local.date().isoformat() for release in second_last] == [
        "2026-01-29",
        "2026-02-26",
    ]


def test_u_sch_09_month_without_any_business_day() -> None:
    # February 2026 has 28 days; make every one of them a non-working day.
    blocked = frozenset(date(2026, 2, 1) + timedelta(days=offset) for offset in range(28))
    spec = CalendarSpec(extra_non_working_days=blocked)
    calendar = make_calendar(spec)
    releases = releases_between(
        monthly_rule(3, calendar=spec),
        datetime(2026, 1, 1, tzinfo=UTC_ZONE),
        datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC_ZONE),
        calendar,
    )
    assert [release.local.date().isoformat() for release in releases] == [
        "2026-01-05",
        "2026-03-04",
    ]


def test_u_sch_10_last_business_day_of_february() -> None:
    calendar = make_calendar()
    leap = releases_between(
        monthly_rule(-1),
        datetime(2024, 2, 1, tzinfo=UTC_ZONE),
        datetime(2024, 2, 29, 23, 59, 59, tzinfo=UTC_ZONE),
        calendar,
    )
    assert [release.local.date().isoformat() for release in leap] == ["2024-02-29"]
    plain = releases_between(
        monthly_rule(-1),
        datetime(2026, 2, 1, tzinfo=UTC_ZONE),
        datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC_ZONE),
        calendar,
    )
    assert [release.local.date().isoformat() for release in plain] == ["2026-02-27"]


def test_u_sch_10_clamping_in_a_short_month() -> None:
    """`business_day: 22` in February 2026 clamps to the last business day, flagged."""
    calendar = make_calendar()
    releases = releases_between(
        monthly_rule(22, at=time(18, 0), timezone=UTC_ZONE),
        datetime(2026, 2, 1, tzinfo=UTC_ZONE),
        datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC_ZONE),
        calendar,
    )
    assert len(releases) == 1
    assert releases[0].local.date() == date(2026, 2, 27)
    assert releases[0].clamped is True
    assert releases[0].local.time() == time(18, 0)


def test_u_sch_13_next_release_after_is_strict_and_bounded() -> None:
    calendar = make_calendar()
    rule = make_rule(CronSchedule("0 6 * * *", UTC_ZONE))
    noon = datetime(2026, 9, 28, 12, 0, tzinfo=UTC_ZONE)

    strict = next_release_after(rule, noon, calendar)
    assert strict is not None
    assert strict.instant == datetime(2026, 9, 29, 6, 0, tzinfo=UTC_ZONE)

    release_instant = datetime(2026, 9, 28, 6, 0, tzinfo=UTC_ZONE)
    strict_at_release = next_release_after(rule, release_instant, calendar)
    assert strict_at_release is not None
    assert strict_at_release.instant == datetime(2026, 9, 29, 6, 0, tzinfo=UTC_ZONE)

    inclusive = next_release_after(rule, release_instant, calendar, inclusive=True)
    assert inclusive is not None
    assert inclusive.instant == release_instant

    bounded = next_release_after(
        rule,
        release_instant,
        calendar,
        inclusive=True,
        until=release_instant,
    )
    assert bounded is not None
    assert bounded.instant == release_instant

    none_within = next_release_after(
        rule,
        release_instant,
        calendar,
        until=release_instant - timedelta(microseconds=1),
    )
    assert none_within is None


def test_u_sch_14_previous_release_at_or_before_includes_equality() -> None:
    calendar = make_calendar()
    rule = make_rule(CronSchedule("0 6 * * *", UTC_ZONE))
    release_instant = datetime(2026, 9, 28, 6, 0, tzinfo=UTC_ZONE)

    at_release = previous_release_at_or_before(rule, release_instant, calendar)
    assert at_release is not None
    assert at_release.instant == release_instant

    before = previous_release_at_or_before(rule, release_instant - timedelta(seconds=1), calendar)
    assert before is not None
    assert before.instant == datetime(2026, 9, 27, 6, 0, tzinfo=UTC_ZONE)

    for instant in (
        release_instant,
        release_instant + timedelta(days=400),
        release_instant - timedelta(days=400),
    ):
        found = previous_release_at_or_before(rule, instant, calendar)
        assert found is not None
        assert found.instant <= instant


def test_u_sch_16_search_horizon_for_a_sparse_schedule() -> None:
    """`0 0 29 2 *` in 2097: the previous release exists, the next is beyond 1830 days."""
    calendar = make_calendar()
    rule = make_rule(CronSchedule("0 0 29 2 *", UTC_ZONE))
    now = datetime(2097, 1, 1, tzinfo=UTC_ZONE)

    previous = previous_release_at_or_before(rule, now, calendar)
    assert previous is not None
    assert previous.instant == datetime(2096, 2, 29, tzinfo=UTC_ZONE)

    # 2100 is not a leap year, so the next candidate is 2104-02-29: outside the horizon.
    assert next_release_after(rule, now, calendar) is None


def test_business_day_releases_never_visit_non_business_days() -> None:
    spec = CalendarSpec(weekend=frozenset(), holiday_calendars=(DE,))
    calendar = make_calendar(spec, {("country DE", 2026): {date(2026, 9, 30): "Holiday"}})
    rule = business_days_rule(calendar=spec)
    releases = releases_between(
        rule,
        datetime(2026, 9, 26, tzinfo=UTC_ZONE),
        datetime(2026, 10, 2, 23, 59, 59, tzinfo=UTC_ZONE),
        calendar,
    )
    assert [release.local.date().isoformat() for release in releases] == [
        "2026-09-26",
        "2026-09-27",
        "2026-09-28",
        "2026-09-29",
        "2026-10-01",
        "2026-10-02",
    ]
