"""Shared pytest fixtures and Hypothesis profiles.

Profiles (selected with ``HYPOTHESIS_PROFILE``, default ``dev``):

- ``dev``: 50 examples, fast enough for the edit loop.
- ``ci``: 300 examples, no deadline (CI hardware is noisy).

``FakeCalendarProvider`` keeps every core test independent of the ``holidays``
library: tests state exactly which holiday dates exist, and a year outside the
configured range raises ``E405`` the same way the real provider does.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date

from hypothesis import settings

from freshcal.core.errors import CalendarError, Issue
from freshcal.core.model import HolidayCalendarRef

settings.register_profile("dev", max_examples=50)
settings.register_profile("ci", max_examples=300, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

DEFAULT_START_YEAR = 1900
DEFAULT_END_YEAR = 2100


class FakeCalendarProvider:
    """A ``CalendarProvider`` whose holidays come from a dict.

    ``holiday_dates`` is keyed by ``(ref label, year)``, for example
    ``{("financial XECB", 2026): {date(2026, 4, 3): "Good Friday"}}``. Years outside
    ``start_year``/``end_year`` raise ``CalendarError`` ``E405``.
    """

    def __init__(
        self,
        holiday_dates: Mapping[tuple[str, int], Mapping[date, str]] | None = None,
        *,
        start_year: int = DEFAULT_START_YEAR,
        end_year: int = DEFAULT_END_YEAR,
    ) -> None:
        self.holiday_dates = dict(holiday_dates or {})
        self.start_year = start_year
        self.end_year = end_year
        self.calls: list[tuple[HolidayCalendarRef, int]] = []

    def holidays(self, ref: HolidayCalendarRef, year: int) -> Mapping[date, str]:
        self.calls.append((ref, year))
        if not self.start_year <= year <= self.end_year:
            raise CalendarError(
                Issue(
                    "E405",
                    f"calendar {ref.label()} has no holiday data for {year} "
                    f"(supported {self.start_year}-{self.end_year})",
                    ref.label(),
                )
            )
        return self.holiday_dates.get((ref.label(), year), {})
