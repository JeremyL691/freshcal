"""Real-publication validation: release dates against what publishers actually did.

Golden tests prove that the code matches the blueprint; these tests prove, with real
public data, that the blueprint's calendar model matches the world (§9.10 A). Each test
compares "FreshCal expects a release on this day" with "the publisher published on this
day" over a complete, committed publication history, using FreshCal's own
``releases_between`` and the real ``HolidaysCalendarProvider``.

A mismatch is a finding to investigate, never an assertion to loosen: a future
``holidays`` release that changes any count must fail here and be explained in
``docs/validation.md``.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from freshcal.adapters.holidays_provider import HolidaysCalendarProvider
from freshcal.config.loader import parse_calendar
from freshcal.config.yaml_loader import load_yaml_file
from freshcal.core.calendar import BusinessCalendar
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    FreshnessTarget,
    HolidayCalendarRef,
    Origin,
    SourceRule,
)
from freshcal.core.schedule import releases_between

VALIDATION = Path(__file__).resolve().parents[2] / "validation"
ECB_DATES = VALIDATION / "ecb" / "publication_dates.csv"
UST_DATES = VALIDATION / "ust" / "publication_dates.csv"
UST_CALENDAR = VALIDATION / "ust" / "calendar.yml"
UTC_ZONE = ZoneInfo("UTC")

ECB_CALENDAR = CalendarSpec(holiday_calendars=(HolidayCalendarRef("financial", "XECB"),))


def read_dates(path: Path) -> list[date]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [date.fromisoformat(row["date"]) for row in csv.DictReader(handle)]


def compare(
    start: date, end: date, calendar: CalendarSpec, publication_file: Path
) -> tuple[set[date], set[date], set[date]]:
    """Return ``(matched, expected_only, published_only)`` for one calendar."""
    published = set(read_dates(publication_file))
    rule = SourceRule(
        source_id="validation",
        origin=Origin.CONFIG,
        schedule=BusinessDaysSchedule(at=time(16, 0), timezone=UTC_ZONE),
        calendar=calendar,
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation="validation", loaded_at_field="_loaded_at"),
    )
    business_calendar = BusinessCalendar(calendar, HolidaysCalendarProvider())
    window_start = datetime.combine(start, time.min, tzinfo=UTC_ZONE)
    window_end = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC_ZONE) - timedelta(
        microseconds=1
    )
    expected = {
        release.local.date()
        for release in releases_between(rule, window_start, window_end, business_calendar)
    }
    return expected & published, expected - published, published - expected


def test_v_01_ecb_publication_dates_match_exactly() -> None:
    """7,102 dates from 1999-01-04 to 2026-09-28, weekdays minus TARGET closing days."""
    matched, expected_only, published_only = compare(
        date(1999, 1, 4), date(2026, 9, 28), ECB_CALENDAR, ECB_DATES
    )
    assert len(matched) == 7102
    assert expected_only == set()
    assert published_only == set()


def test_v_02_treasury_with_the_us_federal_calendar() -> None:
    """`country: US` is the wrong calendar for this source: two false alarms, one miss."""
    matched, expected_only, published_only = compare(
        date(2023, 1, 1),
        date(2025, 12, 31),
        CalendarSpec(holiday_calendars=(HolidayCalendarRef("country", "US"),)),
        UST_DATES,
    )
    assert len(matched) == 748
    assert expected_only == {date(2024, 3, 29), date(2025, 4, 18)}  # Good Friday
    assert published_only == {date(2023, 11, 10)}  # Veterans Day observed


def test_v_03_treasury_with_the_new_york_stock_exchange_calendar() -> None:
    """`financial: XNYS` is worse: five false alarms and two unchecked days."""
    matched, expected_only, published_only = compare(
        date(2023, 1, 1),
        date(2025, 12, 31),
        CalendarSpec(holiday_calendars=(HolidayCalendarRef("financial", "XNYS"),)),
        UST_DATES,
    )
    assert len(matched) == 747
    assert expected_only == {
        date(2023, 10, 9),  # Columbus Day
        date(2024, 10, 14),  # Columbus Day
        date(2024, 11, 11),  # Veterans Day
        date(2025, 10, 13),  # Columbus Day
        date(2025, 11, 11),  # Veterans Day
    }
    assert published_only == {date(2023, 4, 7), date(2025, 1, 9)}


def test_v_04_treasury_with_the_override_calendar_matches_exactly() -> None:
    """The evidence for calendar overrides: two extra non-working days, one working day."""
    loaded = load_yaml_file(UST_CALENDAR)
    assert isinstance(loaded, dict)
    calendar = parse_calendar(loaded, {}, str(UST_CALENDAR), VALIDATION / "ust")
    assert calendar.extra_non_working_days == frozenset({date(2024, 3, 29), date(2025, 4, 18)})
    assert calendar.extra_working_days == frozenset({date(2023, 11, 10)})
    assert calendar.valid_until == date(2025, 12, 31)

    matched, expected_only, published_only = compare(
        date(2023, 1, 1), date(2025, 12, 31), calendar, UST_DATES
    )
    assert len(matched) == 749
    assert expected_only == set()
    assert published_only == set()


def test_publication_files_have_the_expected_shape() -> None:
    ecb_dates = read_dates(ECB_DATES)
    ust_dates = read_dates(UST_DATES)
    assert len(ecb_dates) == 7102
    assert len(ust_dates) == 749
    assert ecb_dates == sorted(ecb_dates)
    assert ust_dates == sorted(ust_dates)
    assert ecb_dates[0] == date(1999, 1, 4)
    assert ecb_dates[-1] == date(2026, 9, 28)
    assert ust_dates[0] == date(2023, 1, 3)
    assert ust_dates[-1] == date(2025, 12, 31)
