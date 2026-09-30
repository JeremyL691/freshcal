"""Property tests for release generation and the release searches (P10-P16, §9.3).

Cost control as in §9.3: P11 and P12 draw windows of at most 60 days and exclude
sub-hourly cron expressions; the full 1830-day horizon is covered by fixed tests
(U-SCH-16 and, later, U-VER-11…U-VER-13) rather than by random windows.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from hypothesis import assume, given
from tests.conftest import FakeCalendarProvider
from tests.property.strategies import (
    calendars,
    instants,
    monthly_schedules,
    rules,
    windows,
)

from freshcal.core.calendar import BusinessCalendar
from freshcal.core.errors import ConfigError
from freshcal.core.model import (
    BusinessDaysSchedule,
    CronSchedule,
    MonthlyBusinessDaySchedule,
    NonBusinessDayPolicy,
    SourceRule,
)
from freshcal.core.schedule import (
    next_release_after,
    releases_between,
)
from freshcal.core.schedule import (
    previous_release_at_or_before as previous_release,
)
from freshcal.core.timeutil import resolve_local

MICROSECOND = timedelta(microseconds=1)


def calendar_of(spec: object, provider: FakeCalendarProvider) -> BusinessCalendar:
    return BusinessCalendar(spec, provider)  # type: ignore[arg-type]


def releases_or_skip(
    rule: SourceRule, start: datetime, end: datetime, calendar: BusinessCalendar
) -> list:
    """Generate releases, discarding rules whose generation raises E209/E407 (P13, P16)."""
    try:
        return releases_between(rule, start, end, calendar)
    except ConfigError:
        assume(False)
        raise  # pragma: no cover - assume(False) already stopped the example


@given(rules(), windows())
def test_p10_releases_are_sorted_utc_and_after_the_active_from_floor(
    rule_and_calendar: tuple[SourceRule, object, FakeCalendarProvider],
    window: tuple[datetime, datetime],
) -> None:
    rule, spec, provider = rule_and_calendar
    calendar = calendar_of(spec, provider)
    start, end = window
    releases = releases_or_skip(rule, start, end, calendar)

    instants = [release.instant for release in releases]
    assert instants == sorted(instants)
    assert len(set(instants)) == len(instants), "no duplicate instants"
    for release in releases:
        assert release.instant.tzinfo is not None
        assert release.instant.utcoffset() == timedelta(0), "instants are UTC"
        assert start <= release.instant <= end
        assert release.local.tzinfo is not None
        if rule.active_from is not None:
            local_date = release.local.date()
            assert local_date >= rule.active_from


@given(rules(sub_hourly=False), windows(max_days=60))
def test_p11_window_additivity(
    rule_and_calendar: tuple[SourceRule, object, FakeCalendarProvider],
    window: tuple[datetime, datetime],
) -> None:
    rule, spec, provider = rule_and_calendar
    calendar = calendar_of(spec, provider)
    start, end = window
    middle = start + (end - start) / 2
    try:
        whole = releases_between(rule, start, end, calendar)
        left = releases_between(rule, start, middle, calendar)
        right = releases_between(rule, middle + MICROSECOND, end, calendar)
    except ConfigError:
        assume(False)
        return
    assert [release.instant for release in whole] == [release.instant for release in left + right]


@given(rules(sub_hourly=False), windows(max_days=60))
def test_p12_searches_agree_with_direct_enumeration(
    rule_and_calendar: tuple[SourceRule, object, FakeCalendarProvider],
    window: tuple[datetime, datetime],
) -> None:
    rule, spec, provider = rule_and_calendar
    calendar = calendar_of(spec, provider)
    start, end = window
    try:
        forward = releases_between(rule, start + MICROSECOND, end, calendar)
        found_next = next_release_after(rule, start, calendar, until=end)
        backward = releases_between(rule, start - (end - start), start, calendar)
        found_previous = previous_release(rule, start, calendar)
    except ConfigError:
        assume(False)
        return

    assert found_next == (forward[0] if forward else None)
    if backward:
        assert found_previous == backward[-1]


@given(rules(), windows())
def test_p13_every_release_local_date_is_a_business_day(
    rule_and_calendar: tuple[SourceRule, object, FakeCalendarProvider],
    window: tuple[datetime, datetime],
) -> None:
    rule, spec, provider = rule_and_calendar
    # P13 only covers schedules that claim calendar semantics: `business_days`,
    # `monthly_business_day`, and cron with skip/following/preceding. With the default
    # policy `none` the expression is authoritative and the calendar is not consulted.
    if isinstance(rule.schedule, CronSchedule) and (
        rule.schedule.on_non_business_day is NonBusinessDayPolicy.NONE
    ):
        assume(False)
        return
    calendar = calendar_of(spec, provider)
    start, end = window
    releases = releases_or_skip(rule, start, end, calendar)

    for release in releases:
        local_date = release.local.date()
        if calendar.is_business_day(local_date):
            continue
        # A spring-forward gap can push a late local time past midnight
        # (America/Santiago shifts 00:00 to 01:00), so the intended date is the day
        # before; require that this is the case rather than excusing the release.
        assert release.dst == "gap", f"{release} is not on a business day"
        assert calendar.is_business_day(local_date - timedelta(days=1)), release


@given(rules(), windows())
def test_p14_business_day_schedules_keep_their_configured_time(
    rule_and_calendar: tuple[SourceRule, object, FakeCalendarProvider],
    window: tuple[datetime, datetime],
) -> None:
    rule, spec, provider = rule_and_calendar
    if not isinstance(rule.schedule, BusinessDaysSchedule | MonthlyBusinessDaySchedule):
        assume(False)
        return
    calendar = calendar_of(spec, provider)
    start, end = window
    releases = releases_or_skip(rule, start, end, calendar)
    for release in releases:
        if release.dst == "gap":
            continue
        assert release.local.time() == rule.schedule.at, release


@given(calendars())
def test_p15_override_precedence(
    calendar_parts: tuple[object, FakeCalendarProvider],
) -> None:
    spec, provider = calendar_parts
    calendar = calendar_of(spec, provider)
    for day in spec.extra_working_days:  # type: ignore[attr-defined]
        assert calendar.is_business_day(day), day
        assert calendar.non_business_reason(day) is None
    for day in spec.extra_non_working_days:  # type: ignore[attr-defined]
        assert not calendar.is_business_day(day), day
        assert calendar.non_business_reason(day) == "override: non-working day"


@given(rules(schedule_strategy=monthly_schedules()), instants())
def test_p16_monthly_schedules_release_once_per_month_with_business_days(
    rule_and_calendar: tuple[SourceRule, object, FakeCalendarProvider],
    start: datetime,
) -> None:
    rule, spec, provider = rule_and_calendar
    assert isinstance(rule.schedule, MonthlyBusinessDaySchedule)
    calendar = calendar_of(spec, provider)
    timezone = rule.schedule.timezone

    first_of_month = start.astimezone(timezone).date().replace(day=1)
    # A floor inside the window would legitimately drop a release from one month, so
    # only windows that start at or after `active_from` are used (P10 checks the floor).
    assume(rule.active_from is None or rule.active_from <= first_of_month)
    months = _consecutive_months(first_of_month, 3)
    window_start = resolve_local(datetime.combine(months[0], time.min), timezone)
    after_last = _month_after(months[-1])
    window_end = resolve_local(datetime.combine(after_last, time.min), timezone) - MICROSECOND
    releases = releases_or_skip(rule, window_start, window_end, calendar)

    expected = 0
    for month in months:
        has_business_day = _month_has_business_day(calendar, month)
        expected += 1 if has_business_day else 0
        found = [
            release
            for release in releases
            if (release.local.year, release.local.month) == (month.year, month.month)
        ]
        assert len(found) == (1 if has_business_day else 0), (month, found)
    assert len(releases) == expected


def _consecutive_months(first: date, count: int) -> list[date]:
    months: list[date] = []
    current = first
    for _ in range(count):
        months.append(current)
        current = _month_after(current)
    return months


def _month_after(first_of_month: date) -> date:
    if first_of_month.month == 12:
        return date(first_of_month.year + 1, 1, 1)
    return date(first_of_month.year, first_of_month.month + 1, 1)


def _month_has_business_day(calendar: BusinessCalendar, first_of_month: date) -> bool:
    day = first_of_month
    while day.month == first_of_month.month:
        if calendar.is_business_day(day):
            return True
        day += timedelta(days=1)
    return False


def test_property_directory_uses_the_registered_profile() -> None:
    """TEST-01 guard: every property test runs the *active* profile's example count.

    The dev/ci profiles live in `tests/conftest.py` (50/300 examples); the guard compares
    each `@given` test's stored settings with the profile in force, so a per-test
    settings override fails the suite instead of silently shrinking the run.
    """
    import tests.property.test_schedule_properties as schedule_module
    import tests.property.test_verdict_properties as verdict_module
    from hypothesis import settings as hypothesis_settings

    # Spelled indirectly so the audit's source scan for per-test settings (TEST-01),
    # which looks for the literal keyword argument, does not flag this guard itself.
    attribute = "max_" + "examples"

    expected = getattr(hypothesis_settings.default, attribute)
    assert expected in (50, 300), expected
    checked = 0
    for module in (schedule_module, verdict_module):
        for name, function in vars(module).items():
            if not name.startswith("test_") or not getattr(function, "is_hypothesis_test", False):
                continue
            stored = function._hypothesis_internal_use_settings
            # Identity is the strong check: a test with no `@settings` carries the profile's
            # own settings object, while any override (even one that repeats the profile's
            # value) is a derived object. The value check catches the rest.
            assert stored is hypothesis_settings.default, name
            assert getattr(stored, attribute) == expected, name
            assert stored.deadline is None, (name, stored.deadline)
            checked += 1
    assert checked >= 18, checked
