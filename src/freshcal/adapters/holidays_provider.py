"""Holiday calendar references backed by the ``holidays`` library.

T-1.4 adds reference validation (:func:`validate_ref`), which the config loader and
the dbt manifest catalog use before a rule is built. The provider that actually
returns holiday dates is added in T-2.2.
"""

from __future__ import annotations

from collections.abc import Iterable

import holidays

from freshcal.core.errors import Issue
from freshcal.core.model import HolidayCalendarRef

__all__ = ["validate_ref"]


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
