"""Tests for release generation: cron schedules (BLUEPRINT.md §3.4.1-§3.4.3)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from tests.conftest import FakeCalendarProvider

from freshcal.core.calendar import BusinessCalendar
from freshcal.core.errors import ConfigError
from freshcal.core.model import (
    CalendarSpec,
    CronSchedule,
    FreshnessTarget,
    HolidayCalendarRef,
    NonBusinessDayPolicy,
    Origin,
    SourceRule,
)
from freshcal.core.schedule import MAX_ROLL_DAYS, releases_between
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
