"""Time utilities: the single implementation of the time rules.

Three properties are worth stating explicitly because everything else depends on them:

1. Every instant inside ``freshcal.core`` is an aware UTC ``datetime``. Public
   functions accept any aware datetime and convert at the boundary; naive input is a
   ``ValueError``, never a guess.
2. Local wall-clock times are converted by exactly one function, :func:`resolve_local`,
   which applies PEP 495 ``fold=0``: a time that does not exist (spring forward) is
   shifted forward by the gap; a time that occurs twice (fall back) resolves to its
   first occurrence.
3. Durations are elapsed time, formatted for humans by :func:`format_duration`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from freshcal.core.errors import ConfigError, Issue

__all__ = [
    "DSTClass",
    "classify_local",
    "format_duration",
    "format_local",
    "format_utc",
    "parse_instant",
    "resolve_local",
    "to_utc",
]

DSTClass = Literal["normal", "gap", "ambiguous"]


def to_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC; a naive value raises ``ValueError``."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"expected a timezone-aware datetime, got {value!r}")
    return value.astimezone(UTC)


def resolve_local(naive_local: datetime, tz: ZoneInfo) -> datetime:
    """Resolve a naive local wall time to the aware UTC instant.

    ``fold=0``: a non-existent wall time is shifted forward by the length of the DST
    gap; an ambiguous wall time keeps its first occurrence.
    """
    if naive_local.tzinfo is not None:
        raise ValueError(f"expected a naive local datetime, got {naive_local!r}")
    return naive_local.replace(tzinfo=tz, fold=0).astimezone(UTC)


def classify_local(naive_local: datetime, tz: ZoneInfo) -> DSTClass:
    """Classify a naive local wall time as ``normal``, ``gap``, or ``ambiguous``."""
    if naive_local.tzinfo is not None:
        raise ValueError(f"expected a naive local datetime, got {naive_local!r}")
    first = naive_local.replace(tzinfo=tz, fold=0)
    second = naive_local.replace(tzinfo=tz, fold=1)
    if first.utcoffset() == second.utcoffset():
        return "normal"
    # Round-trip through UTC: `astimezone(tz)` is a no-op when the value already
    # carries `tz`, so converting directly back would always look equal.
    round_trip = first.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    return "ambiguous" if round_trip == naive_local else "gap"


def format_local(value: datetime, tz: ZoneInfo) -> str:
    """Render an instant in ``tz``, for example ``Fri 2026-09-25 16:00 CEST``."""
    return to_utc(value).astimezone(tz).strftime("%a %Y-%m-%d %H:%M %Z")


def format_utc(value: datetime) -> str:
    """Render an instant as ``2026-09-25T14:00:00Z`` (microseconds kept when non-zero)."""
    return to_utc(value).isoformat().replace("+00:00", "Z")


def format_duration(value: timedelta) -> str:
    """Render a duration for humans: ``1d 2h``, ``1h 45m``, ``<1m``, or ``0m``."""
    seconds = int(value.total_seconds())
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    if seconds == 0:
        return "0m"
    if seconds < 60:
        return f"{sign}<1m"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes = remainder // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return sign + " ".join(parts) if parts else f"{sign}0m"


def parse_instant(text: str, *, flag: str) -> datetime:
    """Parse a CLI instant (``--now``, ``--observed``) into an aware UTC datetime.

    Naive values and unparseable text raise ``ConfigError`` ``E211`` naming the flag,
    because a naive instant silently interpreted in some zone is exactly the class of
    bug the clock and zone rules exist to prevent.
    """

    def invalid() -> ConfigError:
        return ConfigError(
            Issue(
                "E211",
                f"{flag} must be an ISO 8601 date-time with a UTC offset, such as "
                f"2026-09-28T07:30:00+02:00 or 2026-09-28T05:30:00Z; got '{text}'",
            )
        )

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise invalid() from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise invalid()
    return parsed.astimezone(UTC)


def local_date(value: datetime, tz: ZoneInfo) -> date:
    """The local date of an instant in ``tz`` (used for `valid_until` checks)."""
    return to_utc(value).astimezone(tz).date()
