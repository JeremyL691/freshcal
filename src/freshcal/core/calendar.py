"""Business calendar: the §3.3 predicate, rolling, reasons, and expiry (E408).

The predicate is a fixed precedence list, in this order: an explicit working day, an
explicit non-working day, the weekend set, then the union of the holiday calendars.
Explicit user intent therefore beats library data, and a date listed in both override
sets is a configuration error caught at load time (E403).

The calendar also records the latest date anyone looked up. Rule §3.3 uses that for
``valid_until``: evaluating after it fails with ``E408`` (``CONFIG_ERROR``), while
merely consulting later dates (for the next expected arrival) is ``W005``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Literal

from freshcal.core.errors import CalendarError, Issue
from freshcal.core.model import CalendarSpec
from freshcal.core.ports import CalendarProvider

__all__ = ["MAX_ROLL_DAYS", "BusinessCalendar"]

#: A following/preceding roll gives up after this many days (§3.4.3).
MAX_ROLL_DAYS = 31


def _issue(code: str, message: str, location: str | None) -> Issue:
    return Issue(code, message, location)


class BusinessCalendar:
    """A calendar spec plus a provider: answers business-day questions for one rule."""

    def __init__(
        self,
        spec: CalendarSpec,
        provider: CalendarProvider,
        *,
        source_id: str | None = None,
    ) -> None:
        self._spec = spec
        self._provider = provider
        self._source_id = source_id
        self._holiday_cache: dict[int, dict[date, str]] = {}
        self.max_date_looked_up: date | None = None

    @property
    def spec(self) -> CalendarSpec:
        return self._spec

    @property
    def label(self) -> str:
        """Calendar name for messages, ``of <source_id>`` for inline calendars."""
        if self._spec.name is not None:
            return self._spec.name
        if self._source_id is not None:
            return f"of {self._source_id}"
        return "inline"

    def _holidays_for(self, year: int) -> Mapping[date, str]:
        """Union of all holiday calendars for ``year``, remembering which one matched."""
        cached = self._holiday_cache.get(year)
        if cached is None:
            merged: dict[date, str] = {}
            for ref in self._spec.holiday_calendars:
                for day, name in self._provider.holidays(ref, year).items():
                    merged.setdefault(day, f"{name} ({ref.label()})")
            self._holiday_cache[year] = merged
            cached = merged
        return cached

    def _note_lookup(self, day: date) -> None:
        if self.max_date_looked_up is None or day > self.max_date_looked_up:
            self.max_date_looked_up = day

    def holiday_name(self, day: date) -> str | None:
        """The holiday's name and calendar, or ``None`` when the library has no entry."""
        self._note_lookup(day)
        return self._holidays_for(day.year).get(day)

    def is_business_day(self, day: date) -> bool:
        """The predicate of §3.3, in precedence order."""
        if day in self._spec.extra_working_days:
            return True
        if day in self._spec.extra_non_working_days:
            return False
        if day.weekday() in self._spec.weekend:
            return False
        return self.holiday_name(day) is None

    def non_business_reason(self, day: date) -> str | None:
        """Why ``day`` is not a business day, in the wording used by ``explain``."""
        if day in self._spec.extra_working_days:
            return None
        if day in self._spec.extra_non_working_days:
            return "override: non-working day"
        if day.weekday() in self._spec.weekend:
            return "weekend"
        name = self.holiday_name(day)
        if name is not None:
            return f"holiday: {name}"
        return None

    def roll(self, day: date, step: Literal[-1, 1]) -> date:
        """The next (``step=1``) or previous (``step=-1``) business day after ``day``.

        Raises ``CalendarError`` ``E407`` when there is none within ``MAX_ROLL_DAYS``.
        """
        for distance in range(1, MAX_ROLL_DAYS + 1):
            candidate = day + timedelta(days=step * distance)
            if self.is_business_day(candidate):
                return candidate
        direction = "after" if step > 0 else "before"
        raise CalendarError(
            _issue(
                "E407",
                f"no business day within {MAX_ROLL_DAYS} days {direction} {day.isoformat()}",
                self._spec.name,
            )
        )

    def check_valid_at(self, local_date: date) -> None:
        """Raise ``E408`` when ``local_date`` is past the calendar's ``valid_until``."""
        valid_until = self._spec.valid_until
        if valid_until is not None and local_date > valid_until:
            raise CalendarError(
                _issue(
                    "E408",
                    f"calendar {self.label} is valid until {valid_until.isoformat()}, but the "
                    f"evaluation date is {local_date.isoformat()}; review its holidays and "
                    "overrides for the next period and extend valid_until",
                    self._spec.name,
                )
            )

    def consulted_past_valid_until(self) -> date | None:
        """The latest date looked up past ``valid_until``, for warning ``W005``."""
        valid_until = self._spec.valid_until
        if (
            valid_until is not None
            and self.max_date_looked_up is not None
            and self.max_date_looked_up > valid_until
        ):
            return self.max_date_looked_up
        return None
