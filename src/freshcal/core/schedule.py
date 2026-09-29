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
from zoneinfo import ZoneInfo

from croniter import CroniterBadDateError, croniter

from freshcal.core.calendar import MAX_ROLL_DAYS, BusinessCalendar
from freshcal.core.errors import ConfigError, Issue
from freshcal.core.model import (
    BusinessDaysSchedule,
    CronSchedule,
    MonthlyBusinessDaySchedule,
    NonBusinessDayPolicy,
    Release,
    Schedule,
    SourceRule,
)
from freshcal.core.timeutil import classify_local, resolve_local, to_utc

__all__ = [
    "CHUNK",
    "DATE_PADDING_DAYS",
    "HOLD_BACK",
    "MAX_COUNTED_RELEASES",
    "MAX_ROLL_DAYS",
    "SEARCH_HORIZON",
    "Nominal",
    "apply_policy",
    "is_rolling",
    "iter_releases_in_window",
    "next_release_after",
    "no_release_issue",
    "nominal_releases",
    "previous_release_at_or_before",
    "releases_between",
]

SEARCH_HORIZON = timedelta(days=1830)  # 5 * 366
DATE_PADDING_DAYS = 2  # covers DST shifts and date-line offsets at window edges
CHUNK = timedelta(days=32)  # window size for incremental searches
HOLD_BACK = timedelta(hours=6)  # reordering delay for the streaming generator
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
    schedule: CronSchedule,
    calendar: BusinessCalendar,
    d0: date,
    d1: date,
    start_local: datetime | None = None,
) -> Iterator[Nominal]:
    """Nominal releases of a cron expression for local dates in ``[d0, d1]``.

    ``croniter`` only ever sees naive datetimes, so it performs pure wall-clock field
    matching and cannot invent a local time; FreshCal owns DST resolution. ``start_local``
    (naive) overrides where the iteration begins, so a search need not generate the
    thousands of nominals in the padding it would immediately discard.
    """
    start = start_local if start_local is not None else datetime.combine(d0, time.min)
    start = start - timedelta(minutes=1)
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


def _dates(d0: date, d1: date) -> Iterator[date]:
    day = d0
    while day <= d1:
        yield day
        day += timedelta(days=1)


def _months_touching(d0: date, d1: date) -> Iterator[tuple[int, int]]:
    year, month = d0.year, d0.month
    while (year, month) <= (d1.year, d1.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def _dates_of_month(year: int, month: int) -> Iterator[date]:
    day = date(year, month, 1)
    while day.month == month:
        yield day
        day += timedelta(days=1)


def _business_day_nominals(
    schedule: BusinessDaysSchedule, calendar: BusinessCalendar, d0: date, d1: date
) -> Iterator[Nominal]:
    for day in _dates(d0, d1):
        if calendar.is_business_day(day):
            yield Nominal(local=datetime.combine(day, schedule.at))


def _monthly_nominals(
    schedule: MonthlyBusinessDaySchedule, calendar: BusinessCalendar, d0: date, d1: date
) -> Iterator[Nominal]:
    """The Nth (or Nth-from-last) business day of every month touching the window.

    A month with fewer than ``|N|`` business days is clamped to its last (``N > 0``) or
    first (``N < 0``) business day and flagged, because a monthly publisher still
    publishes that month; a month with no business day at all produces no release.
    """
    for year, month in _months_touching(d0, d1):
        business_days = [
            day for day in _dates_of_month(year, month) if calendar.is_business_day(day)
        ]
        if not business_days:
            continue
        if schedule.business_day > 0:
            index = min(schedule.business_day, len(business_days)) - 1
            clamped = schedule.business_day > len(business_days)
        else:
            index = max(schedule.business_day, -len(business_days))
            clamped = -schedule.business_day > len(business_days)
        day = business_days[index]
        if d0 <= day <= d1:
            yield Nominal(local=datetime.combine(day, schedule.at), clamped=clamped)


def nominal_releases(
    schedule: Schedule,
    calendar: BusinessCalendar,
    d0: date,
    d1: date,
    *,
    start_local: datetime | None = None,
) -> Iterator[Nominal]:
    """Nominal releases for local dates in ``[d0, d1]``, inclusive."""
    if isinstance(schedule, CronSchedule):
        yield from _cron_nominals(schedule, calendar, d0, d1, start_local)
    elif isinstance(schedule, BusinessDaysSchedule):
        yield from _business_day_nominals(schedule, calendar, d0, d1)
    else:
        yield from _monthly_nominals(schedule, calendar, d0, d1)


def iter_releases_in_window(
    rule: SourceRule, start: datetime, end: datetime, calendar: BusinessCalendar
) -> Iterator[Release]:
    """``releases_between`` as a stream, yielded in ascending instant order.

    The releases are generated exactly as the list form does — same padding, same
    ``active_from`` floor, same "first nominal release wins" deduplication — but they are
    handed over as soon as no *later* nominal release can precede them. A release is held
    back only while its local time lies within ``HOLD_BACK`` of the newest nominal
    generated so far, because a DST transition can move a release by at most the length
    of the transition (two hours is the largest shift in tzdata's modern era; the bound is
    deliberately generous).

    Streaming matters: a per-minute schedule contains ~46 000 releases in a 32-day
    ``CHUNK``, while the verdict search and the counting cap need only the first few
    thousand. Materialising a whole chunk made a stale per-minute source cost seconds
    (measured 2.3 s for PB-2 before this change).
    """
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
    pending: dict[datetime, tuple[datetime, Release]] = {}
    yielded: set[datetime] = set()
    newest_local: datetime | None = None
    for nominal in nominal_releases(rule.schedule, calendar, d0, d1):
        newest_local = nominal.local
        instant = resolve_local(nominal.local, timezone)
        if floor is not None and instant < floor:
            continue
        if start <= instant <= end and instant not in yielded and instant not in pending:
            pending[instant] = (
                nominal.local,
                Release(
                    instant=instant,
                    local=instant.astimezone(timezone),
                    adjusted_from=nominal.adjusted_from,
                    dst=classify_local(nominal.local, timezone),
                    clamped=nominal.clamped,
                ),
            )
        cutoff = newest_local - HOLD_BACK
        for candidate in sorted(pending):
            nominal_local = pending[candidate][0]
            if nominal_local > cutoff:
                break
            yielded.add(candidate)
            yield pending.pop(candidate)[1]
    for candidate in sorted(pending):
        yielded.add(candidate)
        yield pending[candidate][1]


def releases_between(
    rule: SourceRule, start: datetime, end: datetime, calendar: BusinessCalendar
) -> list[Release]:
    """All releases with ``start <= instant <= end``, sorted ascending, no duplicates."""
    return list(iter_releases_in_window(rule, start, end, calendar))


def _transition_within_hold_back(release: Release, timezone: ZoneInfo) -> bool:
    """True when the schedule zone changes offset within ``HOLD_BACK`` of ``release``.

    A DST transition can make a later nominal release resolve to an *earlier* instant, so
    a candidate found in generation order is only guaranteed to be the earliest when no
    transition is nearby. Comparing the offset at both ends of a ``HOLD_BACK`` window is
    enough because no zone in tzdata changes offset twice within six hours — the smallest
    gap between two consecutive transitions in every zone between 1900 and 2100 is six
    days (``America/Boa_Vista``, 2000-10-15); the check that established this samples
    ``tz.utcoffset`` daily and is repeated in ``tests/unit/core/test_schedule.py``.
    """
    if release.dst != "normal":
        return True
    nominal = release.local.replace(tzinfo=None)
    before = (nominal - HOLD_BACK).replace(tzinfo=timezone, fold=0).utcoffset()
    after = (nominal + HOLD_BACK).replace(tzinfo=timezone, fold=0).utcoffset()
    return before != after


def _candidate_release(
    rule: SourceRule,
    t: datetime,
    lo: datetime,
    hi: datetime,
    calendar: BusinessCalendar,
    *,
    inclusive: bool,
) -> Release | None:
    """The first nominal release in generation order that lies after ``t`` in ``[lo, hi]``."""
    timezone = rule.schedule.timezone
    # Rolling policies can move a release from up to MAX_ROLL_DAYS earlier into the
    # window, so the scan starts that much earlier; without a policy a release can only
    # move by a DST shift of a few hours, which the transition guard covers.
    lookback = MAX_ROLL_DAYS + DATE_PADDING_DAYS if is_rolling(rule.schedule) else 0
    start_local = (lo - timedelta(days=lookback)).astimezone(timezone).replace(tzinfo=None)
    d1 = hi.astimezone(timezone).date() + timedelta(days=DATE_PADDING_DAYS)
    d0 = (start_local - timedelta(days=1)).date()
    floor = (
        resolve_local(datetime.combine(rule.active_from, time.min), timezone)
        if rule.active_from is not None
        else None
    )
    for nominal in nominal_releases(rule.schedule, calendar, d0, d1, start_local=start_local):
        instant = resolve_local(nominal.local, timezone)
        if floor is not None and instant < floor:
            continue
        if not lo <= instant <= hi:
            continue
        if instant > t or (inclusive and instant == t):
            return Release(
                instant=instant,
                local=instant.astimezone(timezone),
                adjusted_from=nominal.adjusted_from,
                dst=classify_local(nominal.local, timezone),
                clamped=nominal.clamped,
            )
    return None


def next_release_after(
    rule: SourceRule,
    t: datetime,
    calendar: BusinessCalendar,
    *,
    inclusive: bool = False,
    until: datetime | None = None,
) -> Release | None:
    """The earliest release strictly after ``t`` (or at ``t`` when ``inclusive``).

    The search walks forward in ``CHUNK``-sized windows, never further than ``until``
    (default ``t + SEARCH_HORIZON``), and returns ``None`` only when the whole range was
    searched. It streams nominal releases and stops at the first one after ``t``: the
    work is proportional to the distance to the answer, not to the window size, which is
    what keeps `next` and the counting path fast for dense schedules. When a DST
    transition lies within ``HOLD_BACK`` of the candidate, the interval up to it is
    enumerated exactly, because a transition can order two releases differently from the
    order their nominal times suggest.
    """
    t = to_utc(t)
    limit = to_utc(until) if until is not None else t + SEARCH_HORIZON

    def qualifies(release: Release) -> bool:
        return release.instant > t or (inclusive and release.instant == t)

    lo = t
    while lo <= limit:
        hi = min(lo + CHUNK, limit)
        candidate = _candidate_release(rule, t, lo, hi, calendar, inclusive=inclusive)
        if candidate is not None:
            if not _transition_within_hold_back(candidate, rule.schedule.timezone):
                return candidate
            earliest = candidate
            for release in iter_releases_in_window(rule, t, candidate.instant, calendar):
                if qualifies(release) and release.instant < earliest.instant:
                    earliest = release
            return earliest
        lo = hi + timedelta(microseconds=1)
    return None


def previous_release_at_or_before(
    rule: SourceRule, t: datetime, calendar: BusinessCalendar
) -> Release | None:
    """The latest release at or before ``t``, searching back at most ``SEARCH_HORIZON``."""
    t = to_utc(t)
    limit = t - SEARCH_HORIZON
    step = timedelta(hours=1)
    hi = t
    while hi >= limit:
        lo = max(hi - step, limit)
        found: Release | None = None
        for release in iter_releases_in_window(rule, lo, hi, calendar):
            found = release  # ascending, so the last one is the answer
        if found is not None:
            return found
        if lo == limit:
            return None
        hi = lo - timedelta(microseconds=1)
        step = min(step * 2, CHUNK)
    return None
