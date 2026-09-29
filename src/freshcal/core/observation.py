"""Observed-timestamp normalization (BLUEPRINT.md §3.7.1).

The warehouse can return three shapes: SQL NULL, a naive timestamp (``TIMESTAMP``),
or an aware one (``TIMESTAMPTZ``). Each is treated differently on purpose:

- **NULL** is not an error: it becomes ``None`` and the verdict layer turns it into
  ``NO_DATA`` (or, with ``active_from``, into a real evaluation).
- **naive** values are interpreted in ``observed_timezone``. There is no implicit
  default, not even UTC: silently reading a Berlin-local column as UTC can turn a
  genuine OVERDUE into ON_TIME (G20a versus G20b), and automated callers act on exit
  codes, not on warnings. A missing zone is therefore ``E214``.
- **aware** values are converted to UTC and ``observed_timezone`` is ignored, with
  ``W002`` when it was set anyway.

A value more than five minutes in the future adds ``W003``; the tolerance absorbs
ordinary clock skew between the warehouse and the machine running FreshCal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from freshcal.core.errors import ConfigError, Issue
from freshcal.core.model import Observation, RawObservation
from freshcal.core.timeutil import format_duration, format_utc, to_utc

__all__ = ["FUTURE_SKEW_TOLERANCE", "normalize_observed"]

FUTURE_SKEW_TOLERANCE = timedelta(minutes=5)


def normalize_observed(
    raw: RawObservation,
    observed_timezone: ZoneInfo | None,
    now: datetime,
    *,
    loaded_at_field: str = "loaded_at_field",
) -> tuple[Observation | None, list[Issue]]:
    """Normalize one raw warehouse value.

    Returns the observation (or ``None`` for SQL NULL) and the warnings it produced.
    Raises ``ConfigError`` ``E214`` for a naive value without a configured zone.

    ``loaded_at_field`` is only used in the ``E214`` message, which is normative and
    names the expression that returned the value (§4.6).
    """
    now = to_utc(now)
    value = raw.value
    if value is None:
        return None, []

    warnings: list[Issue] = []
    if value.tzinfo is None or value.utcoffset() is None:
        if observed_timezone is None:
            raise ConfigError(
                Issue(
                    "E214",
                    f"max({loaded_at_field}) returned a timestamp without time zone "
                    f"({value.isoformat(sep=' ')}); set observed_timezone on the source or "
                    "under defaults (write UTC explicitly if the column stores UTC)",
                )
            )
        instant = value.replace(tzinfo=observed_timezone, fold=0).astimezone(UTC)
        observation = Observation(
            instant=instant,
            raw=value,
            was_naive=True,
            interpreted_timezone=observed_timezone.key,
        )
    else:
        if observed_timezone is not None:
            warnings.append(
                Issue(
                    "W002",
                    f"observed_timezone '{observed_timezone.key}' ignored because the value "
                    "is timezone-aware",
                )
            )
        observation = Observation(
            instant=value.astimezone(UTC),
            raw=value,
            was_naive=False,
            interpreted_timezone=None,
        )

    if observation.instant > now + FUTURE_SKEW_TOLERANCE:
        warnings.append(
            Issue(
                "W003",
                f"observed timestamp {format_utc(observation.instant)} is "
                f"{format_duration(observation.instant - now)} after now; check "
                "observed_timezone or clock skew",
            )
        )
    return observation, warnings
