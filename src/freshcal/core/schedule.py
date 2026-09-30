"""Release generation and release searches.

A *nominal release* is a naive local wall-clock time produced by the schedule before
non-business-day adjustment and DST resolution. Turning a nominal release into a
**release instant** is :func:`resolve_local`'s job (``fold=0``), and a window of
releases is produced by :func:`releases_between`:

1. pad the window so that every nominal that can resolve into it is generated — by
   ``MAX_OFFSET`` on the instant axis (the largest UTC offset in tzdata), plus
   ``MAX_ROLL_DAYS`` for rolling cron schedules (a roll can move a release a month);
2. generate nominal releases for that local range in ascending local order;
3. resolve each one to UTC, drop those before the ``active_from`` floor, keep those
   inside ``[start, end]``, and keep the first nominal release per instant.

Two nominal releases can resolve to the same instant (a rolled weekend collapses
Saturday, Sunday, and Monday onto one Monday; a spring-forward shift can land on a
real scheduled time). One instant is one delivery obligation, so the instant is kept
once and the first nominal release supplies the metadata.

**Ordering.** Nominals are generated in ascending *local* order, but local order is not
always instant order: when a nominal falls inside a DST gap it resolves forward by the
gap length, so a later nominal just after the gap can resolve to an earlier instant
(SEM-02). A nominal's instant lies within ``MAX_OFFSET`` of its wall time (no offset
reaches ``MAX_OFFSET``), so :func:`iter_releases_in_window` holds a release back until a
further ``MAX_OFFSET`` of nominals has been generated; after that no future nominal can
resolve earlier.

The searches are then exact by construction:

- :func:`next_release_after` scans a ``CHUNK``-sized instant window and returns the
  earliest release found in it (taking the minimum instant, so a gap cannot hide an
  earlier release), advancing until a window contains one;
- :func:`previous_release_at_or_before` walks backwards over the same windows and takes
  the last release of the first non-empty one;
- the counting path streams the windowed generator and stops at
  ``MAX_COUNTED_RELEASES``.

The fast path in :func:`_earliest_release` is what keeps dense schedules affordable: the
first qualifying nominal is the answer whenever it is not itself a gap release (an
inversion needs the *earlier* nominal to be inside a gap) and the schedule has no rolling
policy (a roll can also reorder two nominals that fall on the same business day). The
independent oracle in ``tests/oracle/`` is the proof obligation for all of this.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
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
from freshcal.core.timeutil import classify_local, format_utc, resolve_local, to_utc

__all__ = [
    "CHUNK",
    "DATE_PADDING_DAYS",
    "HOLD_BACK",
    "MAX_COUNTED_RELEASES",
    "MAX_ROLL_DAYS",
    "SEARCH_HORIZON",
    "Nominal",
    "apply_policy",
    "consults_calendar",
    "is_rolling",
    "iter_releases_in_window",
    "next_release_after",
    "no_release_issue",
    "nominal_releases",
    "previous_release_at_or_before",
    "releases_between",
]

SEARCH_HORIZON = timedelta(days=1830)  # 5 * 366
DATE_PADDING_DAYS = 2  # local-date padding for date-granular generation bounds
CHUNK = timedelta(days=32)  # window size for incremental searches
MAX_COUNTED_RELEASES = 10_000  # cap for missed/pending counting

# tzdata bounds, measured over every zone and the whole explicit 1900-2100 table
# (`tests/unit/core/test_schedule.py`'s U-SCH-17 re-derives each of them every run):
#   largest |UTC offset|              = 15 h 56 min 08 s  (Asia/Manila, LMT)
#   largest per-zone offset spread    = 25 h 30 min      (Pacific/Apia)
#   largest forward jump              = 24 h             (Kwajalein, 1993-08-21)
#   smallest spacing between changes  = 167 h            (America/Boa_Vista, 2000-10-08)
#                                     =  96 h with pre-1970 history (Africa/Freetown, 1939)
# MAX_OFFSET is the single constant the searches need: it bounds how far a nominal can
# resolve away from its own wall time (so it is the scan padding), how far one transition
# can reorder local time (so it is the streaming hold-back), and - doubled - how far a
# transition can influence an instant, which stays below the smallest spacing (52 h <
# 96 h), so offsets sampled that far apart (and in between) detect every nearby transition.
MAX_OFFSET = timedelta(hours=26)
HOLD_BACK = MAX_OFFSET  # reordering delay of the streaming generator
ROLL_PAD = timedelta(days=MAX_ROLL_DAYS)  # the roll distance as a duration

_ROLLING_POLICIES = frozenset({NonBusinessDayPolicy.FOLLOWING, NonBusinessDayPolicy.PRECEDING})


@dataclass(frozen=True, slots=True)
class Nominal:
    """A naive local wall-clock release before DST resolution and deduplication."""

    local: datetime
    adjusted_from: date | None = None  # original date when a policy rolled it
    clamped: bool = False


def no_release_issue(reference: datetime) -> Issue:
    """``E209``: the schedule produced no release anywhere near ``reference``.

    ``reference`` is the instant the search was asked about — ``now`` for every
    user-visible path — never the padded edge of an internal window (§4.6, SEM-08).
    """
    return Issue(
        "E209",
        f"schedule produces no release within 1830 days before or after {format_utc(reference)}",
    )


def is_rolling(schedule: Schedule) -> bool:
    """True when a schedule can move a release to another local date."""
    return isinstance(schedule, CronSchedule) and schedule.on_non_business_day in _ROLLING_POLICIES


def consults_calendar(schedule: Schedule) -> bool:
    """True when a release depends on business days, i.e. when §3.3 will be consulted.

    Plain cron (``on_non_business_day: none``) never asks the calendar anything, so it has
    no ``valid_until`` exposure either; every other kind and policy does.
    """
    if isinstance(schedule, CronSchedule):
        return schedule.on_non_business_day is not NonBusinessDayPolicy.NONE
    return True


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


def day_or_branches(expression: str) -> tuple[str, str] | None:
    """The two single-branch expressions when standard cron's day-of-month/day-of-week OR applies.

    With *both* fields restricted, standard cron (and §3.4.1) fires when either matches. croniter
    computes that union only while the day-of-week field has no nth-weekday entry — a `#` entry
    makes it drop the day-of-month branch — and it refuses several valid expressions outright
    (`15 0 30 2 0,6`). FreshCal therefore generates each branch on its own and merges the two
    ascending streams, which is the promised OR behaviour (AUD-01, A-19). With either field at
    `*` there is no alternative to compute and ``None`` is returned.
    """
    fields = expression.split()
    if len(fields) != 5:
        return None
    minute, hour, day_of_month, month, day_of_week = fields
    if day_of_month == "*" or day_of_week == "*":
        return None
    return (
        f"{minute} {hour} {day_of_month} {month} *",
        f"{minute} {hour} * {month} {day_of_week}",
    )


class _CronBranch:
    """One single-branch cron stream: ascending naive wall times, or "this branch never fires".

    ``dead`` is set when croniter refuses to produce any next date, which is how a never-firing
    expression such as ``0 0 30 2 *`` behaves. That is not an error by itself — the other branch
    of a day-of-month/day-of-week OR may still fire — so the caller decides, once every branch is
    exhausted, whether the whole expression has no release at all (E209).
    """

    __slots__ = ("_done", "_iterator", "_last_date", "dead")

    def __init__(self, expression: str, start: datetime, last_date: date) -> None:
        self._iterator = croniter(expression, start)
        self._last_date = last_date
        self._done = False
        self.dead = False

    def next(self) -> datetime | None:
        """The next nominal in the window, or ``None`` when this branch has nothing more."""
        if self._done or self.dead:
            return None
        try:
            nominal = self._iterator.get_next(datetime)
        except CroniterBadDateError:
            self.dead = self._done = True
            return None
        if nominal.date() > self._last_date:
            self._done = True
            return None
        return nominal


def _cron_nominals(
    schedule: CronSchedule,
    calendar: BusinessCalendar,
    d0: date,
    d1: date,
    start_local: datetime | None = None,
    reference: datetime | None = None,
) -> Iterator[Nominal]:
    """Nominal releases of a cron expression for local dates in ``[d0, d1]``.

    ``croniter`` only ever sees naive datetimes, so it performs pure wall-clock field
    matching and cannot invent a local time; FreshCal owns DST resolution. ``start_local``
    (naive) overrides where the iteration begins, so a search need not generate the
    thousands of nominals in the padding it would immediately discard. ``reference`` is
    the search instant the E209 message must name (§4.6, SEM-08); only when a caller has
    none at all does it fall back to the window's own first local date.

    A restricted day-of-month **and** day-of-week are OR alternatives (A-19): the two
    single-branch streams are merged in ascending local order and an occurrence both
    branches name is emitted once, so ``on_non_business_day`` is applied exactly once per
    nominal occurrence.
    """
    start = start_local if start_local is not None else datetime.combine(d0, time.min)
    start = start - timedelta(minutes=1)
    if reference is None:
        reference = datetime.combine(d0, time.min, tzinfo=UTC)
    expressions = day_or_branches(schedule.expression) or (schedule.expression,)
    branches = [_CronBranch(expression, start, d1) for expression in expressions]
    live: list[tuple[datetime, _CronBranch]] = []
    for branch in branches:
        nominal = branch.next()
        if nominal is not None:
            live.append((nominal, branch))
    if not live:
        if all(branch.dead for branch in branches):
            # No branch can ever fire: the schedule produces no release anywhere near the
            # search instant, which is a configuration error, not a verdict.
            raise ConfigError(no_release_issue(reference))
        return
    last: datetime | None = None
    while live:
        live.sort(key=lambda item: item[0])
        nominal, branch = live[0]
        following = branch.next()
        if following is None:
            live.pop(0)
        else:
            live[0] = (following, branch)
        if nominal == last:
            continue
        last = nominal
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
    reference: datetime | None = None,
) -> Iterator[Nominal]:
    """Nominal releases for local dates in ``[d0, d1]``, inclusive.

    ``reference`` is only used to name the search instant in an ``E209`` message.
    """
    if isinstance(schedule, CronSchedule):
        yield from _cron_nominals(schedule, calendar, d0, d1, start_local, reference)
    elif isinstance(schedule, BusinessDaysSchedule):
        yield from _business_day_nominals(schedule, calendar, d0, d1)
    else:
        yield from _monthly_nominals(schedule, calendar, d0, d1)


def _offset_stable(instant: datetime, timezone: ZoneInfo) -> bool:
    """True when no offset change can influence order near ``instant``.

    Samples the zone's offset ``2 * MAX_OFFSET`` before ``instant``, at ``instant`` and
    ``2 * MAX_OFFSET`` after it. Three equal samples prove there is no transition in the
    window, because tzdata never changes one zone's offset twice within ``2 * MAX_OFFSET``
    (52 h): each half of the window can hold at most one transition, and one transition
    changes the offset. Two samples would not do: Linux tzdata keeps pre-1970 history
    in which Africa/Freetown changes twice within 96 h and returns to its old offset.
    """
    span = 2 * MAX_OFFSET
    before = (instant - span).astimezone(timezone).utcoffset()
    middle = instant.astimezone(timezone).utcoffset()
    after = (instant + span).astimezone(timezone).utcoffset()
    return before == middle == after


def _hold(rule: SourceRule) -> timedelta:
    """The reordering delay: ``HOLD_BACK``, plus a roll for rolling cron policies."""
    return HOLD_BACK + (ROLL_PAD if is_rolling(rule.schedule) else timedelta(0))


def _scan_lookback(rule: SourceRule, instant: datetime, timezone: ZoneInfo) -> timedelta:
    """How far before ``instant`` a nominal can be and still resolve inside a window there.

    A rolling policy can move a release forward by up to ``MAX_ROLL_DAYS`` (``following``
    moves a Saturday release to Monday), so the scan must start that much earlier; and when
    a transition is near, an earlier wall time can resolve later than the window start by up
    to ``MAX_OFFSET``. When neither applies the scan starts at the window edge, which is
    what keeps dense schedules affordable.
    """
    lookback = ROLL_PAD if is_rolling(rule.schedule) else timedelta(0)
    if not _offset_stable(instant, timezone):
        lookback += MAX_OFFSET
    return lookback


def _floor(rule: SourceRule) -> datetime | None:
    """The instant of ``active_from``'s local midnight, or ``None`` without a floor."""
    if rule.active_from is None:
        return None
    return resolve_local(datetime.combine(rule.active_from, time.min), rule.schedule.timezone)


def iter_releases_in_window(
    rule: SourceRule,
    start: datetime,
    end: datetime,
    calendar: BusinessCalendar,
    *,
    reference: datetime | None = None,
) -> Iterator[Release]:
    """Releases in ``[start, end]``, streamed in ascending instant order, no duplicates.

    Same result as the list form in :func:`releases_between`, but a release is handed over
    as soon as no later nominal release can precede it. A nominal's instant lies within
    ``MAX_OFFSET`` of its wall time, so once ``MAX_OFFSET`` (plus a roll for rolling
    schedules) of *nominals* have been generated past a pending release, every remaining
    nominal must resolve later: that is the hold-back. It is expressed in nominal time and
    compared against the pending instant directly, which is the conservative direction.

    Streaming matters: a per-minute schedule holds ~1 600 releases in the reorder buffer
    while the counting cap needs only 10 000, and the searches stop at their answer instead
    of materialising a whole 32-day ``CHUNK`` (measured 2.3 s for PB-2 before A-9).
    """
    start = to_utc(start)
    end = to_utc(end)
    timezone = rule.schedule.timezone
    hold = _hold(rule)
    scan_local = (
        (start - _scan_lookback(rule, start, timezone)).astimezone(timezone).replace(tzinfo=None)
    )
    end_local = (end + hold).astimezone(timezone).replace(tzinfo=None)
    floor = _floor(rule)
    pending: dict[datetime, Release] = {}
    yielded: set[datetime] = set()
    for nominal in nominal_releases(
        rule.schedule,
        calendar,
        scan_local.date(),
        end_local.date(),
        start_local=scan_local,
        reference=reference if reference is not None else start,
    ):
        if nominal.local >= end_local:
            break
        instant = resolve_local(nominal.local, timezone)
        if floor is not None and instant < floor:
            continue
        if start <= instant <= end and instant not in yielded and instant not in pending:
            pending[instant] = Release(
                instant=instant,
                local=instant.astimezone(timezone),
                adjusted_from=nominal.adjusted_from,
                dst=classify_local(nominal.local, timezone),
                clamped=nominal.clamped,
            )
        cutoff = nominal.local - hold
        for candidate in sorted(pending):
            if candidate.replace(tzinfo=None) > cutoff:
                break
            yielded.add(candidate)
            yield pending.pop(candidate)
    for candidate in sorted(pending):
        yielded.add(candidate)
        yield pending[candidate]


def releases_between(
    rule: SourceRule, start: datetime, end: datetime, calendar: BusinessCalendar
) -> list[Release]:
    """All releases with ``start <= instant <= end``, sorted ascending, no duplicates."""
    return list(iter_releases_in_window(rule, start, end, calendar))


def _make_release(nominal: Nominal, instant: datetime, timezone: ZoneInfo) -> Release:
    return Release(
        instant=instant,
        local=instant.astimezone(timezone),
        adjusted_from=nominal.adjusted_from,
        dst=classify_local(nominal.local, timezone),
        clamped=nominal.clamped,
    )


def _earliest_release(
    rule: SourceRule,
    t: datetime,
    lo: datetime,
    hi: datetime,
    calendar: BusinessCalendar,
    *,
    inclusive: bool,
    reference: datetime,
) -> Release | None:
    """The earliest release in ``[lo, hi]`` that is after ``t`` (or at ``t``).

    The scan starts early enough to see every nominal that can resolve into the window
    (``MAX_OFFSET``, or a roll plus ``MAX_OFFSET`` for rolling schedules; zero when no
    transition is near, which keeps dense schedules cheap), and takes the *minimum* instant
    rather than the first one, because a gap nominal resolves forward and can therefore be
    beaten by a later nominal. The early return is safe only in the two cases the module
    docstring proves: no rolling policy, and a first candidate that is not itself a gap.
    """
    timezone = rule.schedule.timezone
    rolling = is_rolling(rule.schedule)
    scan_local = (lo - _scan_lookback(rule, lo, timezone)).astimezone(timezone).replace(tzinfo=None)
    end_local = (hi + _hold(rule)).astimezone(timezone).replace(tzinfo=None)
    floor = _floor(rule)
    best: Release | None = None
    gap_danger_until: datetime | None = None
    for nominal in nominal_releases(
        rule.schedule,
        calendar,
        scan_local.date(),
        end_local.date(),
        start_local=scan_local,
        reference=reference,
    ):
        if nominal.local >= end_local:
            break
        instant = resolve_local(nominal.local, timezone)
        if floor is not None and instant < floor:
            continue
        qualifies = lo <= instant <= hi and (instant > t or (inclusive and instant == t))
        if qualifies and (best is None or instant < best.instant):
            best = _make_release(nominal, instant, timezone)
            if not rolling:
                if best.dst != "gap":
                    return best
                # A later nominal within the gap's own length cannot be earlier, but the
                # gap can be at most MAX_OFFSET long, so keep scanning that far.
                gap_danger_until = nominal.local + MAX_OFFSET
        if gap_danger_until is not None and nominal.local >= gap_danger_until:
            return best
        if (
            rolling
            and best is not None
            and nominal.local - MAX_OFFSET - ROLL_PAD >= best.instant.replace(tzinfo=None)
        ):
            return best
    return best


def next_release_after(
    rule: SourceRule,
    t: datetime,
    calendar: BusinessCalendar,
    *,
    inclusive: bool = False,
    until: datetime | None = None,
) -> Release | None:
    """The earliest release strictly after ``t`` (or at ``t`` when ``inclusive``).

    The search walks forward in ``CHUNK``-sized instant windows, never further than ``until``
    (default ``t + SEARCH_HORIZON``), and returns ``None`` only when the whole range was
    searched. Each window is exact, so the first window with a release holds the answer and
    the work is proportional to the distance to it.
    """
    t = to_utc(t)
    limit = to_utc(until) if until is not None else t + SEARCH_HORIZON
    lo = t
    while lo <= limit:
        hi = min(lo + CHUNK, limit)
        candidate = _earliest_release(rule, t, lo, hi, calendar, inclusive=inclusive, reference=t)
        if candidate is not None:
            return candidate
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
        for release in iter_releases_in_window(rule, lo, hi, calendar, reference=t):
            found = release  # ascending, so the last one is the answer
        if found is not None:
            return found
        if lo == limit:
            return None
        hi = lo - timedelta(microseconds=1)
        step = min(step * 2, CHUNK)
    return None
