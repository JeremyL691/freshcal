"""Ports: the ``typing.Protocol`` interfaces the core depends on (BLUEPRINT.md §5.4).

Adapters implement these outside the core, so the engine never imports a database
driver, the ``holidays`` library, or a YAML parser. Ports are added here as the tasks
that need them arrive.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Protocol

from freshcal.core.model import FreshnessTarget, HolidayCalendarRef, RawObservation

__all__ = ["CalendarProvider", "FreshnessReader"]


class CalendarProvider(Protocol):
    """Holiday data for one calendar reference and one year."""

    def holidays(self, ref: HolidayCalendarRef, year: int) -> Mapping[date, str]:
        """Return ``holiday date -> name`` for ``year``.

        Raises :class:`~freshcal.core.errors.CalendarError` ``E405`` when the year is
        outside the provider's supported range, because silently returning no holidays
        would turn every weekday into a business day.
        """
        ...


class FreshnessReader(Protocol):
    """Reads one scalar per source from a warehouse, read-only."""

    def read_latest(self, target: FreshnessTarget) -> RawObservation:
        """Run ``SELECT max(loaded_at_field) ...`` and return the value as the driver
        reported it (naive, aware, or ``None`` for SQL NULL).

        Raises :class:`~freshcal.core.errors.QueryError` ``E502`` when the query fails
        and ``E503`` when the value has an unsupported type.
        """
        ...

    def close(self) -> None:
        """Release the connection."""
        ...
