"""Tests for release generation: cron schedules."""

from __future__ import annotations

import zoneinfo
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo, _zoneinfo

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
    HOLD_BACK,
    MAX_OFFSET,
    MAX_ROLL_DAYS,
    _offset_stable,
    day_or_branches,
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
    # T-7.11 / §4.6: the message names the search instant, not a padded window start
    # (T-7.2 named the window start here, which was the last date the search rewrote).
    assert issue.message == (
        "schedule produces no release within 1830 days before or after 2026-01-01T00:00:00Z"
    )


def test_sem_08_e209_names_the_search_instant_in_every_path() -> None:
    """SEM-08: both searches report the instant they were asked about, never a window edge."""
    rule = make_rule(CronSchedule("0 0 30 2 *", UTC_ZONE))
    calendar = make_calendar()
    now = datetime(2026, 2, 10, 12, 0, tzinfo=UTC)
    expected = "schedule produces no release within 1830 days before or after 2026-02-10T12:00:00Z"
    with pytest.raises(ConfigError) as excinfo:
        previous_release_at_or_before(rule, now, calendar)
    assert excinfo.value.issue.message == expected
    with pytest.raises(ConfigError) as excinfo:
        next_release_after(rule, now, calendar)
    assert excinfo.value.issue.message == expected


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


def test_u_sch_17_tzdata_bounds_hold_for_the_search_constants() -> None:
    """U-SCH-17: the constants the searches rely on really hold in this tzdata (T-7.2).

    Derived from every zone's explicit TZif table, 1900-2100 (the same source
    the audit's transition scan used to find the T-6.1 regressions):

    * no two consecutive offset changes are closer than ``2 * MAX_OFFSET`` (52 h), so the
      guard's three samples ``2 * MAX_OFFSET`` apart detect every nearby transition (the
      bound was ``4 * MAX_OFFSET`` with two samples; Linux tzdata's pre-1970 history has
      Africa/Freetown changes 96 h apart, see U-SCH-27);
    * the largest forward jump is below ``HOLD_BACK``; and the largest per-zone offset
      spread is at most ``HOLD_BACK``, so the streaming generator's hold-back is sound;
    * no offset is larger than ``MAX_OFFSET`` in absolute value, so a nominal cannot resolve
      further than ``MAX_OFFSET`` from its own wall time (the scan padding).
    """
    smallest_step: tuple[timedelta, str, datetime] | None = None
    largest_jump: tuple[timedelta, str, datetime] | None = None
    largest_offset: tuple[timedelta, str] | None = None
    largest_spread: tuple[timedelta, str] | None = None
    for name in sorted(zoneinfo.available_timezones()):
        zone = _zoneinfo.ZoneInfo.no_cache(name)
        epochs = zone._trans_utc
        offsets = tuple(info.utcoff for info in zone._ttinfos)
        initial = zone._tti_before.utcoff if zone._tti_before else None
        table = ((initial,) if initial is not None else ()) + offsets
        if not table:  # fixed zones such as UTC carry no explicit table
            probe = ZoneInfo(name).utcoffset(datetime(2000, 1, 1))
            table = (probe or timedelta(0),)
        if largest_offset is None or abs(table[0]) > abs(largest_offset[0]):
            largest_offset = (table[0], name)
        spread = max(table) - min(table)
        if largest_spread is None or spread > largest_spread[0]:
            largest_spread = (spread, name)
        for offset in table:
            if abs(offset) > abs(largest_offset[0]):
                largest_offset = (offset, name)
        changes: list[tuple[float, timedelta, timedelta]] = []
        previous = initial
        for epoch, offset in zip(epochs, offsets, strict=False):
            if previous is not None and offset != previous:
                moment = datetime.fromtimestamp(epoch, UTC)
                if 1900 <= moment.year <= 2100:
                    changes.append((epoch, previous, offset))
            previous = offset
        for (first, _, _), (second, _, _) in pairwise(changes):
            step = timedelta(seconds=second - first)
            if smallest_step is None or step < smallest_step[0]:
                smallest_step = (step, name, datetime.fromtimestamp(first, UTC))
        for epoch, before, after in changes:
            if after > before:
                jump = after - before
                if largest_jump is None or jump > largest_jump[0]:
                    largest_jump = (jump, name, datetime.fromtimestamp(epoch, UTC))

    assert smallest_step is not None
    assert largest_jump is not None
    assert largest_spread is not None
    assert largest_offset is not None
    assert smallest_step[0] > 2 * MAX_OFFSET, smallest_step
    assert largest_jump[0] < HOLD_BACK, largest_jump
    assert largest_spread[0] <= HOLD_BACK, largest_spread
    assert abs(largest_offset[0]) < MAX_OFFSET, largest_offset


def test_u_sch_18_lord_howe_half_hour_gap() -> None:
    """U-SCH-18: a 30-minute gap with two daily fires keeps both releases, in order."""
    zone = ZoneInfo("Australia/Lord_Howe")
    rule = make_rule(CronSchedule("10,30 2 * * *", zone))
    calendar = make_calendar()
    # 2026-10-04 02:00 -> 02:30 local: the 02:10 nominal falls inside the half-hour gap and
    # resolves to 02:40 (+11), *after* the 02:30 nominal's 15:30Z - the one inversion the
    # hold-back exists for. The list below is instant order, which is what callers get.
    start = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)
    end = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)
    releases = releases_between(rule, start, end, calendar)
    instants = [release.instant for release in releases]
    assert instants == sorted(instants)
    assert [release.local.strftime("%Y-%m-%d %H:%M") for release in releases] == [
        "2026-10-04 02:30",
        "2026-10-04 02:40",
        "2026-10-05 02:10",
        "2026-10-05 02:30",
    ]
    assert [release.dst for release in releases] == ["normal", "gap", "normal", "normal"]
    upcoming = next_release_after(rule, datetime(2026, 10, 3, 15, 0, tzinfo=UTC), calendar)
    assert upcoming is not None
    assert (
        upcoming.instant == releases[0].instant == resolve_local(datetime(2026, 10, 4, 2, 30), zone)
    )


def test_u_sch_19_casey_1969_releases_stay_sorted() -> None:
    """U-SCH-19: an 8-hour forward jump (Antarctica/Casey 1969) cannot unsort releases."""
    zone = ZoneInfo("Antarctica/Casey")
    rule = make_rule(CronSchedule("30 1,7,8 * * *", zone))
    calendar = make_calendar()
    releases = releases_between(
        rule,
        datetime(1968, 12, 31, 20, 0, tzinfo=UTC),
        datetime(1969, 1, 1, 9, 0, tzinfo=UTC),
        calendar,
    )
    instants = [release.instant for release in releases]
    assert instants == sorted(instants)
    assert [instant.strftime("%m-%d %H:%MZ") for instant in instants] == [
        "01-01 00:30Z",
        "01-01 01:30Z",
        "01-01 07:30Z",
    ]


def test_u_sch_20_rolled_release_at_a_window_edge() -> None:
    """U-SCH-20: a `following` roll into the window is found from both sides of the edge.

    Kills the padding and look-back mutants (S4, S11): the nominal sits a weekend and a
    holiday before the window start, so a search that only pads by two days or only looks
    back for rolling policies when the window itself is wide misses it.
    """
    zone = ZoneInfo("UTC")
    holidays = {
        ("country DE", 2026): {
            date(2026, 1, 5): "Bridge Monday",
            date(2026, 1, 6): "Bridge Tuesday",
            date(2026, 1, 7): "Bridge Wednesday",
        }
    }
    rule = make_rule(
        CronSchedule("0 6 * * 6", zone, NonBusinessDayPolicy.FOLLOWING),
        calendar=CalendarSpec(holiday_calendars=(DE,)),
    )
    calendar = make_calendar(rule.calendar, holidays)
    # Saturday 2026-01-03 rolls forward over the weekend and three bridge holidays (Mon 01-05,
    # Tue 01-06, Wed 01-07) to Thursday 2026-01-08 (adjusted_from 2026-01-03) - five days after
    # the nominal, so a search that only pads by two days (or does not look back for a rolling
    # policy) misses it.
    nominal = datetime(2026, 1, 3, 6, 0, tzinfo=UTC)
    expected = datetime(2026, 1, 8, 6, 0, tzinfo=UTC)

    inside = releases_between(
        rule, datetime(2026, 1, 8, tzinfo=UTC), datetime(2026, 1, 9, tzinfo=UTC), calendar
    )
    assert [release.instant for release in inside] == [expected]
    assert inside[0].adjusted_from == nominal.date()

    found = next_release_after(rule, nominal + timedelta(minutes=1), calendar)
    assert found is not None
    assert found.instant == expected
    found = next_release_after(rule, datetime(2026, 1, 7, 23, 59, 59, tzinfo=UTC), calendar)
    assert found is not None
    assert found.instant == expected
    assert previous_release_at_or_before(rule, expected, calendar) == found


def test_s6_s7_equal_to_the_business_day_count_is_not_clamped() -> None:
    """Kills mutants S6 and S7: ``clamped`` needs N to *exceed* the business-day count.

    February 2026 has exactly 20 business days, so ``business_day: 20`` selects the last
    one (2026-02-27) and ``business_day: -20`` the first (2026-02-02) as exact matches;
    only 21 / -21 clamp. The ``>=`` mutants flag the exact matches as clamped, which
    would present a regular release as a short-month fallback in reports and traces.
    """
    calendar = make_calendar()
    window_start = datetime(2026, 2, 1, tzinfo=UTC_ZONE)
    window_end = datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC_ZONE)

    for index, expected in ((20, "2026-02-27"), (-20, "2026-02-02")):
        releases = releases_between(monthly_rule(index), window_start, window_end, calendar)
        assert [release.local.date().isoformat() for release in releases] == [expected]
        assert releases[0].clamped is False, index

    for index, expected in ((21, "2026-02-27"), (-21, "2026-02-02")):
        releases = releases_between(monthly_rule(index), window_start, window_end, calendar)
        assert [release.local.date().isoformat() for release in releases] == [expected]
        assert releases[0].clamped is True, index


# ---------------------------------------------------------------------------
# Day-of-month/day-of-week OR semantics (T-8.3, AUD-01, A-19)
# ---------------------------------------------------------------------------


def test_u_sch_21_day_or_branches_only_when_both_fields_are_restricted() -> None:
    """`day_or_branches` splits a restricted pair and leaves every other shape alone."""
    assert day_or_branches("0 9 1 * 1#1") == ("0 9 1 * *", "0 9 * * 1#1")
    assert day_or_branches("15 0 30 2 0,6") == ("15 0 30 2 *", "15 0 * 2 0,6")
    assert day_or_branches("0 9 * * 1#1") is None  # day-of-month is `*`
    assert day_or_branches("0 9 1 * *") is None  # day-of-week is `*`
    assert day_or_branches("0 9 * * *") is None


def test_u_sch_22_the_audit_union_case_has_jan_1_and_jan_5() -> None:
    """AUD-01: `0 9 1 * 1#1` fires on the 1st *and* on the first Monday.

    January 2026 starts on a Thursday, so its first Monday is Jan 5 and `1#1` names only
    that one. The day-of-month branch contributes Jan 1; croniter alone drops it.
    """
    rule = make_rule(CronSchedule("0 9 1 * 1#1", UTC_ZONE))
    calendar = make_calendar()
    january = (
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC),
    )
    releases = releases_between(rule, *january, calendar)
    assert [release.local.day for release in releases] == [1, 5]
    assert all(release.local.hour == 9 for release in releases)

    # Strict next: from just before the Jan 1 release the answer is Jan 1 itself.
    found = next_release_after(rule, datetime(2026, 1, 1, 8, 59, tzinfo=UTC), calendar)
    assert found is not None
    assert found.instant == datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    # Inclusive next at the release instant returns that same release.
    found = next_release_after(
        rule, datetime(2026, 1, 1, 9, 0, tzinfo=UTC), calendar, inclusive=True
    )
    assert found is not None
    assert found.instant == datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    # Bounded next stops at the window edge: Jan 1 is outside a window ending before it.
    bounded = next_release_after(
        rule,
        datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        calendar,
        until=datetime(2026, 1, 1, 8, 59, 59, tzinfo=UTC),
    )
    assert bounded is None
    # Previous: the release before Jan 1 is December 1 2025, the first Monday *and* the 1st.
    previous = previous_release_at_or_before(rule, datetime(2026, 1, 1, 8, 0, tzinfo=UTC), calendar)
    assert previous is not None
    assert previous.instant == datetime(2025, 12, 1, 9, 0, tzinfo=UTC)


def test_u_sch_23_the_policy_is_applied_once_per_nominal_occurrence() -> None:
    """An overlapping occurrence is rolled once, and its metadata is kept (AUD-01).

    Jan 5 2026 is both the 5th and the first Monday, so both branches name it. The union
    must emit it once, and ``following`` must move it exactly one business day when the
    calendar says Jan 5 is not a working day — a second, duplicate nominal would roll a
    second time and collide with the first.
    """
    spec = CalendarSpec(extra_non_working_days=frozenset({date(2026, 1, 5)}))
    rule = make_rule(
        CronSchedule("0 9 5 * 1#1", UTC_ZONE, NonBusinessDayPolicy.FOLLOWING), calendar=spec
    )
    calendar = make_calendar(spec)
    releases = releases_between(
        rule,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC),
        calendar,
    )
    assert [release.local.day for release in releases] == [6]
    assert releases[0].adjusted_from == date(2026, 1, 5)
    assert releases[0].instant == datetime(2026, 1, 6, 9, 0, tzinfo=UTC)


def test_u_sch_24_a_dead_day_of_month_branch_still_fires_on_its_weekday() -> None:
    """`15 0 30 2 0,6` fires on February weekends: the impossible day cannot hide them."""
    rule = make_rule(CronSchedule("15 0 30 2 0,6", UTC_ZONE))
    calendar = make_calendar()
    releases = releases_between(
        rule,
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC),
        calendar,
    )
    assert [release.local.day for release in releases] == [1, 7, 8, 14, 15, 21, 22, 28]
    assert all(release.local.time() == time(0, 15) for release in releases)


def test_u_sch_25_a_never_firing_expression_still_raises_e209() -> None:
    """With the day-of-week field at `*` there is no alternative reading (CLI-03)."""
    rule = make_rule(CronSchedule("0 0 30 2 *", UTC_ZONE))
    calendar = make_calendar()
    with pytest.raises(ConfigError) as excinfo:
        releases_between(
            rule,
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 31, tzinfo=UTC),
            calendar,
        )
    assert excinfo.value.issue.code == "E209"


def test_u_sch_26_multiple_alternatives_keep_every_matching_date() -> None:
    """Several day-of-month days and several nth weekdays all survive the union."""
    rule = make_rule(CronSchedule("0 9 1,15 * 1#1,1#2", UTC_ZONE))
    calendar = make_calendar()
    releases = releases_between(
        rule,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC),
        calendar,
    )
    assert [release.local.day for release in releases] == [1, 5, 12, 15]
    assert len({release.instant for release in releases}) == len(releases)


def _two_transition_zone(first: datetime, spacing: timedelta) -> ZoneInfo:
    """A TZif v1 zone that moves UTC+0 -> UTC+1 at ``first`` and back after ``spacing``."""
    import io
    import struct

    times = (int(first.timestamp()), int((first + spacing).timestamp()))
    abbreviations = b"AAA\x00BBB\x00"
    data = (
        b"TZif"
        + b"\x00" * 16
        + struct.pack(">6l", 0, 0, 0, len(times), 2, len(abbreviations))
        + struct.pack(f">{len(times)}l", *times)
        + bytes((1, 0))
        + struct.pack(">lbB", 0, 0, 0)
        + struct.pack(">lbB", 3600, 1, 4)
        + abbreviations
    )
    return ZoneInfo.from_file(io.BytesIO(data), key="Test/TwoTransitions")


def test_u_sch_27_offset_guard_sees_a_cancelling_pair_of_transitions() -> None:
    """U-SCH-27: two transitions that cancel inside the guard window are still detected.

    Linux tzdata carries pre-1970 history that macOS omits: Africa/Freetown changes its
    offset twice within 95 h in September 1939. Offsets sampled only at both ends of the
    104 h guard window agree there, so the guard must also look at the midpoint.
    """
    first = datetime(2030, 3, 1, 12, tzinfo=UTC)
    zone = _two_transition_zone(first, timedelta(hours=60))
    assert zone.utcoffset(first + timedelta(hours=1)) == timedelta(hours=1)
    assert not _offset_stable(first + timedelta(hours=30), zone)
    assert _offset_stable(first + timedelta(days=30), zone)
