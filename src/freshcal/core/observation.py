"""Observed-timestamp normalization.

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

A value the conversion cannot represent — a timestamp so close to ``datetime.max`` that
applying the configured zone's offset leaves the representable range, for example
``timestamp '9999-12-31 22:00'`` read in ``America/New_York`` — raises ``QueryError``
``E502`` naming the value. It is one source's data problem, never an ``E599`` that
destroys the whole run.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from freshcal.core.errors import ConfigError, Issue, QueryError
from freshcal.core.model import Observation, RawObservation
from freshcal.core.timeutil import format_duration, format_utc, to_utc

__all__ = ["FUTURE_SKEW_TOLERANCE", "normalize_observed"]

FUTURE_SKEW_TOLERANCE = timedelta(minutes=5)


def _unrepresentable(loaded_at_field: str, value: datetime, error: Exception) -> QueryError:
    """The ``E502`` for a value the conversion could not represent, naming the value."""
    return QueryError(
        Issue(
            "E502",
            f"query failed: max({loaded_at_field}) returned {value.isoformat(sep=' ')}, "
            f"which cannot be represented as an instant ({type(error).__name__}: {error})",
        )
    )


def normalize_observed(
    raw: RawObservation,
    observed_timezone: ZoneInfo | None,
    now: datetime,
    *,
    loaded_at_field: str = "loaded_at_field",
) -> tuple[Observation | None, list[Issue]]:
    """Normalize one raw warehouse value.

    Returns the observation (or ``None`` for SQL NULL) and the warnings it produced.
    Raises ``ConfigError`` ``E214`` for a naive value without a configured zone, and
    ``QueryError`` ``E502`` for a value whose conversion overflows ``datetime``.

    ``loaded_at_field`` is only used in the ``E214``/``E502`` messages, which are
    normative and name the expression that returned the value.
    """
    now = to_utc(now)
    value = raw.value
    if value is None:
        return None, []

    warnings: list[Issue] = []
    try:
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
    except (OverflowError, ValueError) as error:
        raise _unrepresentable(loaded_at_field, value, error) from error

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
