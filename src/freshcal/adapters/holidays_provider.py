"""Holiday calendars backed by the ``holidays`` library (BLUEPRINT.md §7.5).

:func:`validate_ref` checks a reference at load time (T-1.4);
:class:`HolidaysCalendarProvider` answers the ``CalendarProvider`` port at evaluation
time (T-2.2). The library's supported years are read from the instance, and a year
outside them raises ``E405`` — the library itself silently returns no holidays, which
would turn every weekday into a business day.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date

import holidays

from freshcal.core.errors import CalendarError, Issue
from freshcal.core.model import HolidayCalendarRef

__all__ = ["HolidaysCalendarProvider", "validate_ref"]


def _valid(values: Iterable[str]) -> str:
    """Render a sorted, comma-separated list of valid values."""
    return ", ".join(sorted(values))


def validate_ref(ref: HolidayCalendarRef, location: str) -> Issue | None:
    """Return an issue when the reference is unknown to the ``holidays`` library.

    Country, subdivision, and category problems are ``E401``; unknown financial
    markets are ``E402``. Returns ``None`` when the reference is usable.
    """
    if ref.kind == "financial":
        markets = holidays.list_supported_financial()
        if ref.code not in markets:
            return Issue(
                "E402",
                f"{location}: unknown financial market '{ref.code}'; valid: {_valid(markets)}",
                location,
            )
        return None

    countries = holidays.list_supported_countries()
    if ref.code not in countries:
        return Issue("E401", f"{location}: unknown country '{ref.code}'", location)
    subdivisions = countries[ref.code]
    if ref.subdivision is not None and ref.subdivision not in subdivisions:
        return Issue(
            "E401",
            f"{location}: unknown subdivision '{ref.subdivision}' for {ref.code}; "
            f"valid: {_valid(subdivisions)}",
            location,
        )
    supported = holidays.country_holidays(ref.code).supported_categories
    for category in ref.categories:
        if category not in supported:
            return Issue(
                "E401",
                f"{location}: unknown category '{category}' for {ref.code}; "
                f"valid: {_valid(supported)}",
                location,
            )
    return None


class HolidaysCalendarProvider:
    """A ``CalendarProvider`` backed by the ``holidays`` library.

    Results are cached per ``(ref, year)``, so repeated calls with the same arguments
    return the same mapping object.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[HolidayCalendarRef, int], Mapping[date, str]] = {}

    def holidays(self, ref: HolidayCalendarRef, year: int) -> Mapping[date, str]:
        """Holiday dates and names for one year; ``E405`` outside the supported range."""
        key = (ref, year)
        cached = self._cache.get(key)
        if cached is None:
            cached = self._load(ref, year)
            self._cache[key] = cached
        return cached

    def _load(self, ref: HolidayCalendarRef, year: int) -> Mapping[date, str]:
        if ref.kind == "financial":
            instance = holidays.financial_holidays(ref.code, years=year)
        else:
            instance = holidays.country_holidays(
                ref.code,
                subdiv=ref.subdivision,
                years=year,
                categories=ref.categories,
            )
        start = instance.start_year
        end = instance.end_year
        if not start <= year <= end:
            raise CalendarError(
                Issue(
                    "E405",
                    f"calendar {ref.label()} has no holiday data for {year} "
                    f"(supported {start}-{end})",
                    ref.label(),
                )
            )
        return dict(instance.items())
