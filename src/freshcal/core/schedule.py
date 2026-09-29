"""Release generation and release searches (BLUEPRINT.md §3.4).

A *nominal release* is a naive local wall-clock time produced by the schedule before
non-business-day adjustment and DST resolution. Turning a nominal release into a
**release instant** is :func:`resolve_local`'s job (``fold=0``), and a window of
releases is produced by :func:`releases_between`:

1. pad the window by ``DATE_PADDING_DAYS`` (plus ``MAX_ROLL_DAYS`` for rolling cron
   schedules) so that rolled or DST-shifted releases near the edges are not missed;
2. generate nominal releases for the padded local date range;
3. resolve each one to UTC, drop those before the ``active_from`` floor, keep those
   inside ``[start, end]``, and keep the first nominal release per instant.

Two nominal releases can resolve to the same instant (a rolled weekend collapses
Saturday, Sunday, and Monday onto one Monday; a spring-forward shift can land on a
real scheduled time). One instant is one delivery obligation, so the instant is kept
once and the first nominal release supplies the metadata.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from croniter import CroniterBadDateError, croniter

from freshcal.core.calendar import MAX_ROLL_DAYS, BusinessCalendar
from freshcal.core.errors import ConfigError, Issue
from freshcal.core.model import (
    CronSchedule,
    NonBusinessDayPolicy,
    Release,
    Schedule,
    SourceRule,
)
from freshcal.core.timeutil import classify_local, resolve_local, to_utc

__all__ = [
    "CHUNK",
    "DATE_PADDING_DAYS",
    "MAX_COUNTED_RELEASES",
    "MAX_ROLL_DAYS",
    "SEARCH_HORIZON",
    "Nominal",
    "apply_policy",
    "is_rolling",
    "no_release_issue",
    "nominal_releases",
    "releases_between",
]

SEARCH_HORIZON = timedelta(days=1830)  # 5 * 366
DATE_PADDING_DAYS = 2  # covers DST shifts and date-line offsets at window edges
CHUNK = timedelta(days=32)  # window size for incremental searches
MAX_COUNTED_RELEASES = 10_000  # cap for missed/pending counting

_ROLLING_POLICIES = frozenset({NonBusinessDayPolicy.FOLLOWING, NonBusinessDayPolicy.PRECEDING})


@dataclass(frozen=True, slots=True)
class Nominal:
    """A naive local wall-clock release before DST resolution and deduplication."""

    local: datetime
    adjusted_from: date | None = None  # original date when a policy rolled it
    clamped: bool = False


def no_release_issue(reference: str) -> Issue:
    """``E209``: the schedule produced no release anywhere near ``reference``."""
    return Issue(
        "E209", f"schedule produces no release within 1830 days before or after {reference}"
    )


def is_rolling(schedule: Schedule) -> bool:
    """True when a schedule can move a release to another local date."""
    return isinstance(schedule, CronSchedule) and schedule.on_non_business_day in _ROLLING_POLICIES


def apply_policy(
    nominal: datetime, policy: NonBusinessDayPolicy, calendar: BusinessCalendar
) -> Iterator[Nominal]:
    """Apply ``on_non_business_day`` to one nominal release (§3.4.3).

    With ``policy == NONE`` the calendar is not consulted at all, so a plain cron
    expression keeps plain cron's meaning and no holiday lookups are recorded.
    """
    if policy is NonBusinessDayPolicy.NONE or calendar.is_business_day(nominal.date()):
        yield Nominal(local=nominal)
    elif policy is NonBusinessDayPolicy.SKIP:
        return
    elif policy is NonBusinessDayPolicy.FOLLOWING:
        rolled = calendar.roll(nominal.date(), 1)
        yield Nominal(local=datetime.combine(rolled, nominal.time()), adjusted_from=nominal.date())
    elif policy is NonBusinessDayPolicy.PRECEDING:
        rolled = calendar.roll(nominal.date(), -1)
        yield Nominal(local=datetime.combine(rolled, nominal.time()), adjusted_from=nominal.date())


def _cron_nominals(
    schedule: CronSchedule, calendar: BusinessCalendar, d0: date, d1: date
) -> Iterator[Nominal]:
    """Nominal releases of a cron expression for local dates in ``[d0, d1]``.

    ``croniter`` only ever sees naive datetimes, so it performs pure wall-clock field
    matching and cannot invent a local time; FreshCal owns DST resolution.
    """
    start = datetime.combine(d0, time.min) - timedelta(minutes=1)
    iterator = croniter(schedule.expression, start)
    while True:
        try:
            nominal = iterator.get_next(datetime)
        except CroniterBadDateError as error:
            # A never-firing expression such as "0 0 30 2 *" passes is_valid but has
            # no next date at all; that is a configuration error, not a verdict.
            raise ConfigError(no_release_issue(d0.isoformat())) from error
        if nominal.date() > d1:
            return
        yield from apply_policy(nominal, schedule.on_non_business_day, calendar)


def nominal_releases(
    schedule: Schedule, calendar: BusinessCalendar, d0: date, d1: date
) -> Iterator[Nominal]:
    """Nominal releases for local dates in ``[d0, d1]``, inclusive."""
    if isinstance(schedule, CronSchedule):
        yield from _cron_nominals(schedule, calendar, d0, d1)
        return
    raise NotImplementedError(f"{type(schedule).__name__} generation is implemented in T-2.4")


def releases_between(
    rule: SourceRule, start: datetime, end: datetime, calendar: BusinessCalendar
) -> list[Release]:
    """All releases with ``start <= instant <= end``, sorted ascending, no duplicates."""
    start = to_utc(start)
    end = to_utc(end)
    timezone = rule.schedule.timezone
    padding = DATE_PADDING_DAYS + (MAX_ROLL_DAYS if is_rolling(rule.schedule) else 0)
    d0 = start.astimezone(timezone).date() - timedelta(days=padding)
    d1 = end.astimezone(timezone).date() + timedelta(days=padding)
    floor = (
        resolve_local(datetime.combine(rule.active_from, time.min), timezone)
        if rule.active_from is not None
        else None
    )
    by_instant: dict[datetime, Release] = {}
    for nominal in nominal_releases(rule.schedule, calendar, d0, d1):
        instant = resolve_local(nominal.local, timezone)
        if floor is not None and instant < floor:
            continue
        if start <= instant <= end and instant not in by_instant:
            by_instant[instant] = Release(
                instant=instant,
                local=instant.astimezone(timezone),
                adjusted_from=nominal.adjusted_from,
                dst=classify_local(nominal.local, timezone),
                clamped=nominal.clamped,
            )
    return [by_instant[key] for key in sorted(by_instant)]
