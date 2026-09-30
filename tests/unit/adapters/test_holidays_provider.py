"""Tests for the ``holidays``-backed provider.

These tests use the real library on purpose: they pin a handful of facts (XECB 2026,
DE versus DE-BY, CN's National Day week, the supported-year ranges) that the golden
scenarios and the real-data validation depend on. If a future ``holidays`` release
changes them, the failure is a data change to investigate, not a test to weaken.
"""

from __future__ import annotations

from datetime import date

import pytest

from freshcal.adapters.holidays_provider import HolidaysCalendarProvider, validate_ref
from freshcal.core.errors import CalendarError
from freshcal.core.model import HolidayCalendarRef

XECB = HolidayCalendarRef("financial", "XECB")
DE = HolidayCalendarRef("country", "DE")
DE_BY = HolidayCalendarRef("country", "DE", subdivision="BY")
CN = HolidayCalendarRef("country", "CN")


def test_acceptance_xecb_2026() -> None:
    """T-2.2 acceptance: the six TARGET closing days of 2026."""
    provider = HolidaysCalendarProvider()
    assert sorted(day.isoformat() for day in provider.holidays(XECB, 2026)) == [
        "2026-01-01",
        "2026-04-03",
        "2026-04-06",
        "2026-05-01",
        "2026-12-25",
        "2026-12-26",
    ]


def test_de_subdivision_adds_epiphany() -> None:
    provider = HolidaysCalendarProvider()
    bavaria = provider.holidays(DE_BY, 2026)
    national = provider.holidays(DE, 2026)
    assert date(2026, 1, 6) in bavaria
    assert date(2026, 1, 6) not in national
    assert date(2026, 1, 1) in national


def test_cn_national_day_week() -> None:
    provider = HolidaysCalendarProvider()
    holidays_2026 = provider.holidays(CN, 2026)
    for day in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-05", "2026-10-06", "2026-10-07"):
        assert date.fromisoformat(day) in holidays_2026


@pytest.mark.parametrize("year", [1998, 2101])
def test_xecb_out_of_range_is_e405(year: int) -> None:
    provider = HolidaysCalendarProvider()
    with pytest.raises(CalendarError) as excinfo:
        provider.holidays(XECB, year)
    issue = excinfo.value.issue
    assert issue.code == "E405"
    assert issue.message == (
        f"calendar financial XECB has no holiday data for {year} (supported 1999-2100)"
    )


def test_repeated_calls_return_the_cached_mapping() -> None:
    provider = HolidaysCalendarProvider()
    first = provider.holidays(XECB, 2026)
    assert provider.holidays(XECB, 2026) is first
    assert provider.holidays(XECB, 2027) is not first


def test_validate_ref_accepts_known_references() -> None:
    assert validate_ref(XECB, "loc") is None
    assert validate_ref(DE, "loc") is None
    assert validate_ref(DE_BY, "loc") is None
    assert validate_ref(CN, "loc") is None
    assert (
        validate_ref(HolidayCalendarRef("country", "CN", categories=("public", "half_day")), "loc")
        is None
    )
