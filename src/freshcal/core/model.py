"""Core domain types.

Everything here is frozen: an evaluation is a pure function of these values, and
nothing in the core mutates them. Datetime fields named ``instant``, ``deadline``,
``evaluated_at``, or ``next_expected_arrival`` are aware UTC; the two dataclasses
built by adapters and the application layer (``Observation`` and
``EvaluationResult``) reject naive values in ``__post_init__``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import IntEnum, StrEnum
from typing import Literal
from zoneinfo import ZoneInfo

from freshcal.core.errors import Issue

__all__ = [
    "BusinessDaysSchedule",
    "CalendarSpec",
    "CheckReport",
    "CronSchedule",
    "EvaluationResult",
    "FreshnessTarget",
    "HolidayCalendarRef",
    "MonthlyBusinessDaySchedule",
    "NextEntry",
    "NextRelease",
    "NextReport",
    "NonBusinessDayPolicy",
    "Observation",
    "Origin",
    "RawObservation",
    "Release",
    "Schedule",
    "SourceEntry",
    "SourceRule",
    "Status",
    "Weekday",
    "mark_duplicate_source_ids",
]


class Status(StrEnum):
    """The single classification of one evaluation."""

    ON_TIME = "ON_TIME"
    NOT_DUE = "NOT_DUE"
    OVERDUE = "OVERDUE"
    NO_DATA = "NO_DATA"
    CONFIG_ERROR = "CONFIG_ERROR"
    QUERY_ERROR = "QUERY_ERROR"

    @property
    def is_verdict(self) -> bool:
        """True for the three statuses that describe the data, not the check."""
        return self in (Status.ON_TIME, Status.NOT_DUE, Status.OVERDUE)


class Weekday(IntEnum):
    """Weekday numbering that matches ``datetime.date.weekday()``."""

    MON = 0
    TUE = 1
    WED = 2
    THU = 3
    FRI = 4
    SAT = 5
    SUN = 6


class NonBusinessDayPolicy(StrEnum):
    """What a ``cron`` schedule does when a nominal release is not on a business day."""

    NONE = "none"
    SKIP = "skip"
    FOLLOWING = "following"
    PRECEDING = "preceding"


class Origin(StrEnum):
    """Where a rule came from."""

    CONFIG = "config"
    DBT_MANIFEST = "dbt_manifest"


@dataclass(frozen=True, slots=True)
class HolidayCalendarRef:
    """A reference to one calendar of the ``holidays`` library."""

    kind: Literal["country", "financial"]
    code: str  # "DE", "XECB"
    subdivision: str | None = None
    categories: tuple[str, ...] = ("public",)  # country only

    def label(self) -> str:
        """Human-readable label, for example ``country DE-BY`` or ``financial XECB``."""
        if self.kind == "financial":
            return f"financial {self.code}"
        if self.subdivision is not None:
            return f"country {self.code}-{self.subdivision}"
        return f"country {self.code}"


@dataclass(frozen=True, slots=True)
class CalendarSpec:
    """Weekend, holiday calendars, and overrides for one rule."""

    weekend: frozenset[Weekday] = frozenset({Weekday.SAT, Weekday.SUN})
    holiday_calendars: tuple[HolidayCalendarRef, ...] = ()
    extra_working_days: frozenset[date] = frozenset()
    extra_non_working_days: frozenset[date] = frozenset()
    valid_until: date | None = None  # None = no expiry
    name: str | None = None  # set for named calendars


@dataclass(frozen=True, slots=True)
class CronSchedule:
    """A 5-field cron expression evaluated as pure wall-clock matching."""

    expression: str
    timezone: ZoneInfo
    on_non_business_day: NonBusinessDayPolicy = NonBusinessDayPolicy.NONE


@dataclass(frozen=True, slots=True)
class BusinessDaysSchedule:
    """A fixed local time on every business day of the calendar."""

    at: time
    timezone: ZoneInfo


@dataclass(frozen=True, slots=True)
class MonthlyBusinessDaySchedule:
    """A fixed local time on the Nth (or Nth-from-last) business day of the month."""

    business_day: int  # -23..-1 or 1..23
    at: time
    timezone: ZoneInfo


Schedule = CronSchedule | BusinessDaysSchedule | MonthlyBusinessDaySchedule


@dataclass(frozen=True, slots=True)
class FreshnessTarget:
    """The warehouse coordinates: relation, timestamp expression, optional filter."""

    relation: str
    loaded_at_field: str
    filter: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRule:
    """The fully resolved configuration of one source."""

    source_id: str
    origin: Origin
    schedule: Schedule
    calendar: CalendarSpec
    grace: timedelta
    target: FreshnessTarget
    observed_timezone: ZoneInfo | None = None  # None = not configured anywhere
    active_from: date | None = None


@dataclass(frozen=True, slots=True)
class Release:
    """A release instant plus the metadata that explains how it was produced."""

    instant: datetime  # aware UTC
    local: datetime  # aware, schedule time zone
    adjusted_from: date | None = None  # original date when rolled
    dst: Literal["normal", "gap", "ambiguous"] = "normal"
    clamped: bool = False


@dataclass(frozen=True, slots=True)
class RawObservation:
    """The warehouse value exactly as returned: naive, aware, or ``None``."""

    value: datetime | None


@dataclass(frozen=True, slots=True)
class Observation:
    """A raw value after normalization."""

    instant: datetime  # aware UTC
    raw: datetime
    was_naive: bool
    interpreted_timezone: str | None  # IANA key used for naive values

    def __post_init__(self) -> None:
        _require_aware("Observation.instant", self.instant)


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """The outcome of evaluating one source."""

    source_id: str
    origin: Origin
    status: Status
    evaluated_at: datetime
    schedule_timezone: str | None  # None only for CONFIG_ERROR without a parsed schedule
    release: Release | None
    deadline: datetime | None
    observation: Observation | None
    next_expected_arrival: datetime | None
    missed_count: int = 0
    missed_truncated: bool = False
    pending_count: int = 0
    explanation: str = ""
    warnings: tuple[Issue, ...] = ()
    error: Issue | None = None

    def __post_init__(self) -> None:
        _require_aware("EvaluationResult.evaluated_at", self.evaluated_at)
        if self.deadline is not None:
            _require_aware("EvaluationResult.deadline", self.deadline)
        if self.next_expected_arrival is not None:
            _require_aware("EvaluationResult.next_expected_arrival", self.next_expected_arrival)


@dataclass(frozen=True, slots=True)
class SourceEntry:
    """One configured source, valid or not (output of a ``SourceCatalog``)."""

    source_id: str
    origin: Origin
    rule: SourceRule | None  # None when invalid
    errors: tuple[Issue, ...] = ()
    warnings: tuple[Issue, ...] = ()  # load-time warnings (W004, W006), copied into results
    location: str = ""  # "sources[2]" or "dbt:source.proj.ecb.fx_rates"


@dataclass(frozen=True, slots=True)
class CheckReport:
    """All results of one ``check`` run, sorted by ``source_id``."""

    evaluated_at: datetime
    results: tuple[EvaluationResult, ...]
    exit_code: int


@dataclass(frozen=True, slots=True)
class NextRelease:
    """One upcoming release, as ``freshcal next`` reports it."""

    instant: datetime  # aware UTC
    local: datetime  # aware, schedule time zone
    deadline: datetime  # aware UTC


@dataclass(frozen=True, slots=True)
class NextEntry:
    """The upcoming releases of one source (``freshcal next``).

    ``warnings`` is required by the JSON report contract (§8.1): a calendar consulted
    past its ``valid_until`` produces ``W005`` on the entry whose releases depend on it.
    """

    source_id: str
    schedule_timezone: str | None
    releases: tuple[NextRelease, ...] = ()
    warnings: tuple[Issue, ...] = ()
    error: Issue | None = None


@dataclass(frozen=True, slots=True)
class NextReport:
    """All upcoming releases of one ``next`` run, sorted by ``source_id``."""

    evaluated_at: datetime
    sources: tuple[NextEntry, ...]


def mark_duplicate_source_ids(entries: Sequence[SourceEntry]) -> list[SourceEntry]:
    """Mark every entry whose source ID occurs more than once with ``E206``.

    Both the duplicate and the entry it duplicates are marked: with two definitions of
    the same source, neither can be trusted to be the one the operator meant. The message
    names **every** location of the conflict and is identical for every member, and the
    entry's own errors stay in the list next to it (CFG-21).
    """
    locations: dict[str, list[str]] = {}
    for entry in entries:
        locations.setdefault(entry.source_id, []).append(entry.location)

    marked: list[SourceEntry] = []
    for entry in entries:
        group = locations[entry.source_id]
        if len(group) < 2:
            marked.append(entry)
            continue
        issue = Issue("E206", _duplicate_message(entry.source_id, group), entry.location)
        marked.append(
            SourceEntry(
                source_id=entry.source_id,
                origin=entry.origin,
                rule=None,
                errors=(issue, *entry.errors),
                warnings=entry.warnings,
                location=entry.location,
            )
        )
    return marked


def _duplicate_message(source_id: str, locations: Sequence[str]) -> str:
    """``duplicate source id 'id' at a and b`` for two, a comma list for more."""
    if len(locations) == 2:
        listed = f"{locations[0]} and {locations[1]}"
    else:
        listed = f"{', '.join(locations[:-1])} and {locations[-1]}"
    return f"duplicate source id '{source_id}' at {listed}"


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got {value!r}")
