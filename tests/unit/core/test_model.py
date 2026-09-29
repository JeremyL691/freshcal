"""Tests for the core domain model and the issue catalog (BLUEPRINT.md §5.3, §4.6)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from freshcal.core.errors import ISSUE_CODES
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    CheckReport,
    EvaluationResult,
    FreshnessTarget,
    HolidayCalendarRef,
    Observation,
    Origin,
    Release,
    SourceRule,
    Status,
    Weekday,
)

ACTIVE_CODES = {
    # E1xx
    "E100",
    "E101",
    "E102",
    "E103",
    "E104",
    "E105",
    "E106",
    "E110",
    # E2xx
    "E201",
    "E202",
    "E203",
    "E204",
    "E205",
    "E206",
    "E207",
    "E208",
    "E209",
    "E210",
    "E211",
    "E212",
    "E213",
    "E214",
    "E215",
    # E3xx
    "E301",
    "E302",
    "E303",
    "E304",
    # E4xx
    "E401",
    "E402",
    "E403",
    "E404",
    "E405",
    "E406",
    "E407",
    "E408",
    # E5xx
    "E501",
    "E502",
    "E503",
    "E504",
    "E505",
    "E599",
    # W-codes
    "W002",
    "W003",
    "W004",
    "W005",
    "W006",
}


def make_result(**overrides: object) -> EvaluationResult:
    """A minimal valid EvaluationResult, with fields replaceable per test."""
    defaults: dict[str, object] = {
        "source_id": "ecb.fx_rates",
        "origin": Origin.CONFIG,
        "status": Status.ON_TIME,
        "evaluated_at": datetime(2026, 9, 28, 5, 30, tzinfo=UTC),
        "schedule_timezone": "Europe/Berlin",
        "release": None,
        "deadline": None,
        "observation": None,
        "next_expected_arrival": None,
    }
    defaults.update(overrides)
    return EvaluationResult(**defaults)  # type: ignore[arg-type]


def test_u_mod_01_status_is_verdict() -> None:
    assert [s.value for s in Status if s.is_verdict] == ["ON_TIME", "NOT_DUE", "OVERDUE"]
    assert not Status.NO_DATA.is_verdict
    assert not Status.CONFIG_ERROR.is_verdict
    assert not Status.QUERY_ERROR.is_verdict


def test_u_mod_02_dataclasses_are_frozen() -> None:
    release = Release(
        instant=datetime(2026, 9, 25, 14, 0, tzinfo=UTC),
        local=datetime(2026, 9, 25, 16, 0, tzinfo=ZoneInfo("Europe/Berlin")),
    )
    rule = SourceRule(
        source_id="ecb.fx_rates",
        origin=Origin.CONFIG,
        schedule=BusinessDaysSchedule(at=time(16, 0), timezone=ZoneInfo("Europe/Berlin")),
        calendar=CalendarSpec(),
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation="raw.fx_rates", loaded_at_field="_loaded_at"),
    )
    for instance, field in (
        (release, "instant"),
        (rule, "source_id"),
        (CalendarSpec(), "valid_until"),
        (make_result(), "status"),
        (
            CheckReport(
                evaluated_at=datetime(2026, 9, 28, 5, 30, tzinfo=UTC), results=(), exit_code=0
            ),
            "exit_code",
        ),
    ):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, field, None)


def test_u_mod_03_naive_datetimes_are_rejected() -> None:
    naive = datetime(2026, 9, 28, 5, 30)
    aware = datetime(2026, 9, 28, 5, 30, tzinfo=UTC)

    with pytest.raises(ValueError, match="evaluated_at must be timezone-aware"):
        make_result(evaluated_at=naive)
    with pytest.raises(ValueError, match="deadline must be timezone-aware"):
        make_result(deadline=naive)
    with pytest.raises(ValueError, match="next_expected_arrival must be timezone-aware"):
        make_result(next_expected_arrival=naive)
    with pytest.raises(ValueError, match="instant must be timezone-aware"):
        Observation(instant=naive, raw=naive, was_naive=True, interpreted_timezone="UTC")

    # Aware values are accepted, including non-UTC ones.
    result = make_result(
        evaluated_at=aware,
        deadline=aware,
        next_expected_arrival=aware,
    )
    assert result.evaluated_at == aware


def test_u_mod_04_issue_catalog_is_exactly_the_active_codes() -> None:
    assert set(ISSUE_CODES) == ACTIVE_CODES
    assert len(ACTIVE_CODES) == 46
    assert "W001" not in ISSUE_CODES  # retired in blueprint 1.1, never reuse
    assert all(ISSUE_CODES[code] for code in ISSUE_CODES)


def test_u_mod_05_weekday_matches_date_weekday() -> None:
    assert [weekday.value for weekday in Weekday] == [0, 1, 2, 3, 4, 5, 6]
    monday = date(2026, 9, 28)
    assert monday.weekday() == Weekday.MON
    for offset in range(7):
        assert (monday + timedelta(days=offset)).weekday() == Weekday(offset)


def test_holiday_calendar_ref_label() -> None:
    assert HolidayCalendarRef("financial", "XECB").label() == "financial XECB"
    assert HolidayCalendarRef("country", "DE").label() == "country DE"
    assert HolidayCalendarRef("country", "DE", subdivision="BY").label() == "country DE-BY"
