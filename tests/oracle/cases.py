"""Seeded case generation and comparison helpers for the differential oracle tests (T-7.1).

Reconstructed from the reviewer's `fuzz.py` (which imported a `fuzz_space.py` that is not part
of `review/v0.1.0/`): the zone list is the union of the zones the recorded findings use and the
zones the plan names, and the cron list is the union of the expressions the findings use and the
reviewer's documented never-firing set. Everything here is *input generation*: it must never
encode the expected answer, only draw a case the oracle and the implementation both answer.

The generator deliberately mixes three case families:
- random rules over 2001-2037 (all three kinds, all four policies, weekend/override calendars);
- instants adjacent to real tzdata transitions (gaps, overlaps, date-line jumps) — read from the
  zone objects, so the suite follows whatever tzdata the venv ships;
- 2026-2027 in every zone, the range the golden rows and the review's fuzzers concentrate on.
"""

from __future__ import annotations

import bisect
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import cache, lru_cache, partial
from zoneinfo import ZoneInfo, _zoneinfo

from freshcal.core.errors import CalendarError, Issue
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    CronSchedule,
    HolidayCalendarRef,
    MonthlyBusinessDaySchedule,
    NonBusinessDayPolicy,
    Weekday,
)

# The 25 zones: every zone the recorded findings name, plus the plan's examples.
ZONES = [
    "UTC",
    "Africa/Casablanca",
    "America/Asuncion",
    "America/Caracas",
    "America/Havana",
    "America/New_York",
    "America/Santiago",
    "America/Sao_Paulo",
    "America/St_Johns",
    "Antarctica/Casey",
    "Antarctica/Troll",
    "Antarctica/Vostok",
    "Asia/Beirut",
    "Asia/Gaza",
    "Asia/Kathmandu",
    "Asia/Kolkata",
    "Australia/Lord_Howe",
    "Australia/Sydney",
    "Europe/Berlin",
    "Europe/Dublin",
    "Europe/Moscow",
    "Pacific/Apia",
    "Pacific/Chatham",
    "Pacific/Fakaofo",
    "Pacific/Kiritimati",
]

# The reviewer's never-firing expressions (croniter raises CroniterBadDateError for them).
# Five are genuinely never-firing; four are valid §3.4.1 `day_or` schedules that croniter
# refuses (finding SEM-08: "croniter refuses some valid OR-semantics expressions"). T-7.11
# rejects those four at load time with E202, so the implementation never silently reports
# "no release" for a schedule that does fire.
NEVER_FIRES = (
    "0,30 16 30 2 *",
    "10-20/5 1-3 30 2 *",
    "30 22 30 2 *",
    "5 9 31 2 *",
    "0,30 0,12 30 2 1-5",
    "10-20/5 0 31 2 1-5",
    "10-20/5 0,12 30 2 1-5",
    "15 0 30 2 0,6",
    "59 16 31 2 6",
)
CRONITER_DAY_OR_REFUSALS = (
    "0,30 0,12 30 2 1-5",
    "10-20/5 0 31 2 1-5",
    "10-20/5 0,12 30 2 1-5",
    "15 0 30 2 0,6",
    "59 16 31 2 6",
)

# Restricted day-of-month plus an nth-weekday day-of-week (T-8.2). Standard cron ORs the two
# restricted fields, so the answer is the union of the two branches; croniter computes only
# the nth-weekday branch and silently drops the day-of-month one, so its output is a strict
# *subset* and cannot be the reference truth here. `test_oracle_selfcheck` checks the union
# against hand-derived cases and only checks that croniter's answer is contained in it.
DOM_HASH_UNIONS = (
    "0 9 1 * 1#1",
    "0 9 1,15 * 1#1,1#2",
    "0 9 29 * 1#5",
    "30 2 31 * 0#5",
    "10-20/5 0 15 * 1#1,1#2",
)

# The nth-weekday grammar the oracle implements: `N#O` with N a weekday (0-7, Sunday=0) and
# O the occurrence in the month (1-5). A fifth occurrence is absent in months that have only
# four such weekdays. Mixing plain weekdays with `#` in one field is refused (see
# `oracle.Cron.parse`); the `H`/`R` extensions are not part of the grammar.
NTH_WEEKDAY_EXPRESSIONS = (
    "0 9 * * 1#1",
    "0 9 * * 5#5",
    "0 9 * * 0#3",
    "0 9 * * 1#1,1#2",
)

# Every expression the recorded findings use, plus the never-firing set.
CRON_EXPRS = [
    "* * * * *",
    "*/15 0,12 * * 6",
    "*/15 2 * * *",
    "*/30 * * * 0,6",
    "*/30 * * 3,10 0,6",
    "*/30 16 30 * 5",
    "0 * * * *",
    "0 0 * * 0,6",
    "0 0 29 2 *",
    "0 16 * * 0,6",
    "0 16 * 1-6 6",
    "0 6 L * *",
    "0 9 * * 1",
    "0 9 1 * *",
    "0 9 15 * *",
    "0 9,18 * * 1,3",
    "0,30 16 31 * 5",
    "0,30 2 * * *",
    "0,30 2 * * 5",
    "10,40 1,2,3 * * *",
    "10-20/5 * 30 * 5",
    "10-20/5 0 * * 0,6",
    "10-20/5 1-3 * * 6",
    "10-20/5 23 * * 0,6",
    "10-20/5 3 * 1-6 6",
    "15 1-3 * * 6",
    "15 16 * * 1",
    "15 16 * * 5",
    "15 16 * 2 6",
    "15 2 * * 1",
    "15 2 1,15 2 *",
    "15 22 * * *",
    "15 9 * * 6",
    "30 * * 1-6 5",
    "30 16 * * 5",
    "30 2 * * *",
    "45 1-3 * * *",
    "45 3 * 3,10 5",
    "45 3 15 * 1",
    "5 */6 * * 5",
    "5 0 29 3,10 0,6",
    "5 2,3 29 * 5",
    "59 0,12 * * 1",
    "59 1 * 2 0,6",
    "59 22 * * 6",
    "59 3 * 2 6",
    *NEVER_FIRES,
    *NTH_WEEKDAY_EXPRESSIONS,
    *DOM_HASH_UNIONS,
]

US = timedelta(microseconds=1)
REF = HolidayCalendarRef("country", "FAKE")


class SeededProvider:
    """Random holidays for *every* year (unlike the property tests' 2024-2030 only)."""

    def __init__(self, seed: int, density: int) -> None:
        self.seed = seed
        self.density = density
        self._cache: dict[int, dict[date, str]] = {}

    def holidays(self, ref: HolidayCalendarRef, year: int) -> dict[date, str]:
        if year not in self._cache:
            rng = random.Random(f"{self.seed}-{year}")
            count = rng.randint(0, self.density)
            self._cache[year] = {
                date(year, 1, 1) + timedelta(days=rng.randrange(365)): "H" for _ in range(count)
            }
        return self._cache[year]


class BoundedProvider:
    """Wraps a provider with a supported year range; outside it raises E405 like the real one."""

    def __init__(self, inner, start_year: int, end_year: int) -> None:
        self.inner = inner
        self.start_year = start_year
        self.end_year = end_year

    def holidays(self, ref: HolidayCalendarRef, year: int) -> dict[date, str]:
        if not self.start_year <= year <= self.end_year:
            raise CalendarError(Issue("E405", f"no holiday data for {year}"))
        return self.inner.holidays(ref, year)


@cache
def transitions(key: str, lo: int = 1970, hi: int = 2040) -> tuple[datetime, ...]:
    """Offset-change instants of ``key`` between ``lo`` and ``hi``, from the explicit TZif table."""
    zone = _zoneinfo.ZoneInfo.no_cache(key)
    previous = zone._tti_before.utcoff if zone._tti_before else None
    out: list[datetime] = []
    for timestamp, info in zip(zone._trans_utc, zone._ttinfos, strict=False):
        if previous is not None and info.utcoff != previous:
            moment = datetime.fromtimestamp(timestamp, UTC)
            if lo <= moment.year <= hi:
                out.append(moment)
        previous = info.utcoff
    return tuple(out)


def offset_changes(zone: ZoneInfo, lo: datetime, hi: datetime) -> list[datetime]:
    """Offset-change instants of ``zone`` inside ``[lo, hi]`` (for the ±26 h neighbourhoods)."""
    key = zone.key
    return [moment for moment in transitions(key) if lo <= moment <= hi]


def rand_rule(rng: random.Random, focus: str = "all") -> tuple[object, frozenset[Weekday]]:
    """A random schedule and weekend set; ``focus`` narrows the family (gap/rolling/bd/monthly)."""
    zone = ZoneInfo(rng.choice(ZONES))
    kinds = ["cron"] * 4 + ["bd", "monthly"]
    if focus in ("cron", "gap", "rolling"):
        kinds = ["cron"]
    elif focus in ("bd", "monthly"):
        kinds = [focus]
    kind = rng.choice(kinds)
    if kind == "cron":
        expression = rng.choice(CRON_EXPRS)
        if focus == "gap":
            expression = rng.choice(
                [
                    "30 2 * * *",
                    "0,30 2 * * *",
                    "15 0 * * *",
                    "45 1-3 * * *",
                    "10,40 1,2,3 * * *",
                    "0 * * * *",
                    "*/15 2 * * *",
                ]
            )
        policy = rng.choice(list(NonBusinessDayPolicy))
        if focus == "rolling":
            policy = rng.choice([NonBusinessDayPolicy.FOLLOWING, NonBusinessDayPolicy.PRECEDING])
            expression = rng.choice(
                [
                    "0 9 * * 1",
                    "0 9 15 * *",
                    "0 9 1 * *",
                    "30 16 * * 5",
                    "0 6 L * *",
                    "0 9,18 * * 1,3",
                    "0 0 * * 0,6",
                ]
            )
        schedule: object = CronSchedule(expression, zone, policy)
    elif kind == "bd":
        schedule = BusinessDaysSchedule(
            time(rng.randrange(24), rng.choice([0, 15, 30, 45, 59])), zone
        )
    else:
        value = rng.choice([v for v in range(-23, 24) if v != 0])
        schedule = MonthlyBusinessDaySchedule(
            value, time(rng.randrange(24), rng.choice([0, 30])), zone
        )
    count = rng.choice([2, 2, 2, 0, 1, 3, 4, 5, 6])
    if count == 2 and rng.random() >= 0.3:
        weekend = frozenset({Weekday.SAT, Weekday.SUN})
    else:
        weekend = frozenset(rng.sample(list(Weekday), count))
    return schedule, weekend


def rand_instant(
    rng: random.Random, zone_key: str, focus: str, *, near_transition_chance: float = 0.6
) -> datetime:
    """An instant, biased towards real offset changes (and the plan's 2026-2027 band)."""
    moments = transitions(zone_key)
    if moments and (focus == "gap" or rng.random() < near_transition_chance):
        moment = rng.choice(moments)
        if rng.random() < 0.5:
            return moment + timedelta(minutes=rng.randrange(0, 240))
        return moment + timedelta(minutes=rng.randrange(-3 * 1440, 3 * 1440))
    if rng.random() < 0.25:
        moment = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=rng.randrange(0, 2 * 525600))
        return moment
    return datetime(2001, 1, 1, tzinfo=UTC) + timedelta(minutes=rng.randrange(0, 36 * 525600))


def build(rng: random.Random, focus: str = "all"):
    """A rule, a provider and a reference instant: one differential case."""
    from freshcal.core.model import FreshnessTarget, Origin, SourceRule

    schedule, weekend = rand_rule(rng, focus)
    instant = rand_instant(rng, schedule.timezone.key, focus)  # type: ignore[attr-defined]
    near = instant.astimezone(schedule.timezone).date()  # type: ignore[attr-defined]
    plus = frozenset(
        near + timedelta(days=rng.randrange(-40, 40)) for _ in range(rng.choice([0, 0, 1, 3]))
    )
    minus = (
        frozenset(
            near + timedelta(days=rng.randrange(-40, 40)) for _ in range(rng.choice([0, 0, 1, 3]))
        )
        - plus
    )
    spec = CalendarSpec(
        weekend=weekend,
        holiday_calendars=(REF,),
        extra_working_days=plus,
        extra_non_working_days=minus,
    )
    grace = rng.choice(
        [
            timedelta(0),
            timedelta(minutes=1),
            timedelta(minutes=30),
            timedelta(hours=1),
            timedelta(hours=2),
            timedelta(hours=6),
            timedelta(days=1),
            timedelta(days=3),
            timedelta(minutes=rng.randrange(0, 3 * 1440)),
        ]
    )
    active_from = None if rng.random() < 0.8 else near + timedelta(days=rng.randrange(-30, 10))
    rule = SourceRule(
        source_id="oracle.case",
        origin=Origin.CONFIG,
        schedule=schedule,  # type: ignore[arg-type]
        calendar=spec,
        grace=grace,
        target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
        observed_timezone=None,
        active_from=active_from,
    )
    provider = SeededProvider(rng.randrange(10**9), rng.choice([0, 3, 10, 20]))
    return rule, provider, instant


def is_dense(rule) -> bool:
    """True for cron expressions that fire many times a day (short comparison spans)."""
    if not isinstance(rule.schedule, CronSchedule):
        return False
    hours = rule.schedule.expression.split()[1]
    minutes = rule.schedule.expression.split()[0]
    return hours in ("*", "1-3", "2,3") or "*" in minutes


def describe(rule) -> dict[str, object]:
    """A JSON-able description of a case, used in failure messages and finding records."""
    schedule = rule.schedule
    base: dict[str, object] = {
        "tz": schedule.timezone.key,
        "grace": str(rule.grace),
        "active_from": str(rule.active_from),
        "weekend": sorted(int(w) for w in rule.calendar.weekend),
        "X+": sorted(map(str, rule.calendar.extra_working_days)),
        "X-": sorted(map(str, rule.calendar.extra_non_working_days)),
    }
    if isinstance(schedule, CronSchedule):
        base.update(
            kind="cron", expr=schedule.expression, policy=schedule.on_non_business_day.value
        )
    elif isinstance(schedule, BusinessDaysSchedule):
        base.update(kind="business_days", at=str(schedule.at))
    else:
        base.update(kind="monthly", n=schedule.business_day, at=str(schedule.at))
    return base


def release_tuple(release) -> list[str | bool] | None:
    """The compared shape of a `freshcal.core.model.Release`."""
    if release is None:
        return None
    return [release.instant.isoformat(), str(release.adjusted_from), release.dst, release.clamped]


def oracle_release_tuple(release) -> list[str | bool] | None:
    """The compared shape of an `oracle.ORel`."""
    if release is None:
        return None
    return [release.instant.isoformat(), str(release.adjusted_from), release.dst, release.clamped]


def nearest_transitions(zone: ZoneInfo, lo: datetime, hi: datetime) -> tuple[datetime, ...]:
    """Offset-change instants of ``zone`` inside ``[lo, hi]`` (for the ±26 h neighbourhoods)."""
    return tuple(moment for moment in transitions(zone.key) if lo <= moment <= hi)


def local_midnight_utc(day: date, zone: ZoneInfo) -> datetime:
    """The UTC instant of local midnight on ``day`` (fold 0), for boundary cases."""
    return datetime.combine(day, time.min).replace(tzinfo=zone, fold=0).astimezone(UTC)


@lru_cache(maxsize=64)
def _epoch_table(key: str) -> tuple[tuple[float, ...], tuple[timedelta, ...]]:
    """The zone's explicit transition epochs and the UTC offset valid from each one."""
    zone = _zoneinfo.ZoneInfo.no_cache(key)
    epochs = tuple(zone._trans_utc)
    offsets = tuple(info.utcoff for info in zone._ttinfos)
    return epochs, offsets


def offsets_in_effect(key: str, utc_lo: datetime, utc_hi: datetime) -> frozenset[timedelta]:
    """Offsets in effect at any instant of ``[utc_lo, utc_hi]``, read from the TZif table.

    This is the self-check's independent derivation: the oracle samples ``astimezone`` every
    30 minutes, this walks the zone's own transition table, so agreeing means two different
    readings of tzdata match.
    """
    epochs, offsets = _epoch_table(key)
    if not epochs:  # fixed zones such as UTC carry no explicit transition table
        return frozenset({ZoneInfo(key).utcoffset(datetime(2000, 1, 1))})
    lo = utc_lo.timestamp()
    hi = utc_hi.timestamp()
    first = bisect.bisect_right(epochs, lo) - 1
    if first < 0:
        return frozenset({offsets[0]})
    seen: set[timedelta] = set()
    for index in range(first, len(epochs)):
        if epochs[index] > hi:
            break
        seen.add(offsets[index])
    return frozenset(seen)


def table_preimages(wall: datetime, key: str) -> tuple[list[datetime], list[datetime]]:
    """(candidates, valid) for a naive wall time, from the TZif table (oracle-independent)."""
    utc_lo = (wall - timedelta(hours=26)).replace(tzinfo=UTC)
    utc_hi = (wall + timedelta(hours=26)).replace(tzinfo=UTC)
    zone = ZoneInfo(key)
    candidates = sorted(
        {(wall - offset).replace(tzinfo=UTC) for offset in offsets_in_effect(key, utc_lo, utc_hi)}
    )
    valid = [u for u in candidates if u.astimezone(zone).replace(tzinfo=None) == wall]
    return candidates, valid


def table_resolve(wall: datetime, key: str) -> datetime:
    """§3.2.3 by hand: earliest preimage, or the latest candidate (gap shifted forward)."""
    candidates, valid = table_preimages(wall, key)
    return min(valid) if valid else max(candidates)


def table_classify(wall: datetime, key: str) -> str:
    """§3.2.3 by hand: normal / ambiguous / gap."""
    _, valid = table_preimages(wall, key)
    if len(valid) == 1:
        return "normal"
    return "ambiguous" if len(valid) > 1 else "gap"


# ---------------------------------------------------------------- differential comparison


@dataclass(frozen=True, slots=True)
class Outcome:
    """One side's answer to one compared operation (T-8.1, AUD-06).

    ``value`` is the compared answer when the side answered; ``code`` is the typed failure
    code when it refused instead. A legitimate "no such release" is a value (``None``), not
    a refusal; the implementation's ``E209`` ("no release within the 1830-day search
    horizon") is recorded as a refusal whose code is ``E209`` and compared by
    :func:`_refusals_agree`.
    """

    value: object = None
    refused: bool = False
    code: str | None = None


def _product_outcome(call) -> Outcome:
    """Run one implementation operation, recording its value or its typed refusal."""
    from freshcal.core.errors import ConfigError

    try:
        return Outcome(value=call())
    except ConfigError as error:  # includes CalendarError
        return Outcome(refused=True, code=error.issue.code)


def _oracle_outcome(call, oracle_module) -> Outcome:
    """Run one oracle operation the same way; its refusals carry an error code too."""
    from freshcal.core.errors import ConfigError

    try:
        return Outcome(value=call())
    except oracle_module.OracleError as error:
        return Outcome(refused=True, code=error.code)
    except ConfigError as error:
        return Outcome(refused=True, code=error.issue.code)


def _refusals_agree(got: str | None, want: str | None) -> bool:
    """Whether two typed refusals are compatible answers to the same question.

    Identical codes agree. Beyond that, the implementation's ``E209`` states that the
    schedule has no release anywhere in its 1830-day search, which entails the oracle's
    bounded "no release in my 400-day window" answer: the stronger statement cannot
    contradict the weaker one, so ``E209`` and "no refusal" are compatible in either
    direction. Every other combination — ``E209`` against ``E407``, or a refusal against
    a value — is a disagreement.
    """
    if got == want:
        return True
    return {got, want} <= {None, "E209"}


def _empty_answer(value: object) -> bool:
    """True for the oracle's *bounded* "nothing there" answers: ``None`` and ``[]``.

    ``o_first_after``/``o_last_before`` return ``None`` and ``o_releases`` returns ``[]``
    when their window holds no release. The window is always smaller than the
    implementation's 1830-day horizon, so such an answer cannot contradict an
    implementation ``E209``; it also cannot confirm it, which is why the relation is
    recorded here rather than treated as agreement between two refusals.
    """
    return value is None or value == []


def _agree(
    got: Outcome,
    want: Outcome,
    to_product=lambda value: value,
    to_oracle=lambda value: value,
    tolerate=None,
) -> bool:
    """Whether the two outcomes are compatible.

    A refusal on exactly one side is a disagreement, with one documented exception: the
    implementation's ``E209`` ("no release within 1830 days") against the oracle's bounded
    "nothing in my smaller window". ``tolerate`` is the second explicit escape, for the
    oracle's bounded look-around: it receives the two raw values and returns True only when
    the comparison is genuinely not comparable (a value the oracle's window could not see).
    """
    if got.refused or want.refused:
        if got.refused and want.refused:
            return _refusals_agree(got.code, want.code)
        refusal, answer = (got, want) if got.refused else (want, got)
        return refusal.code == "E209" and _empty_answer(answer.value)
    if tolerate is not None and tolerate(got.value, want.value):
        return True
    return bool(to_product(got.value) == to_oracle(want.value))


def _shown(outcome: Outcome, to_value) -> object:
    """The recorded form of one outcome for a difference record."""
    return outcome.code if outcome.refused else to_value(outcome.value)


def compare_case(
    rule, provider, instant, rng: random.Random, oracle
) -> list[tuple[str, dict, object, object]]:
    """Run one case against the oracle; return a list of `(check, args, got, want)` disagreements.

    Every operation is evaluated on **both sides independently** (T-8.1, AUD-06): each side
    yields either a value or a typed refusal, and the operation is reported when exactly one
    side refuses, when both refuse with incompatible codes, or when both values differ. A
    refusal is never read as "both sides failed", so a one-sided failure can no longer hide
    behind an operation that happened to match, and the remaining operations are still
    checked. The value comparison keeps the reviewer's bounded look-around rules: the oracle
    surveys 400 days for the verdict context, so a value outside that window is explicitly
    not comparable and an unknown answer is never treated as agreement.
    """
    from freshcal.core.calendar import BusinessCalendar
    from freshcal.core.model import RawObservation
    from freshcal.core.schedule import (
        next_release_after,
        previous_release_at_or_before,
        releases_between,
    )
    from freshcal.core.verdict import evaluate

    calendar = BusinessCalendar(rule.calendar, provider)
    oracle_calendar = oracle.OCal(rule.calendar, provider)
    differences: list[tuple[str, dict, object, object]] = []

    span = (
        timedelta(hours=rng.randrange(1, 48))
        if is_dense(rule)
        else timedelta(hours=rng.randrange(1, 24 * 40))
    )
    start, end = instant, instant + span

    got = _product_outcome(
        lambda: [release_tuple(x) for x in releases_between(rule, start, end, calendar)]
    )
    want = _oracle_outcome(
        lambda: [
            oracle_release_tuple(x) for x in oracle.o_releases(rule, oracle_calendar, start, end)
        ],
        oracle,
    )
    if not _agree(got, want):
        differences.append(
            (
                "releases_between",
                {"A": start.isoformat(), "B": end.isoformat()},
                _shown(got, lambda value: value[:6]),
                _shown(want, lambda value: value[:6]),
            )
        )

    found = _product_outcome(lambda: next_release_after(rule, instant, calendar))
    expected = _oracle_outcome(
        lambda: oracle.o_first_after(
            rule, oracle_calendar, instant + US, instant + timedelta(days=400)
        ),
        oracle,
    )
    if not _agree(
        found,
        expected,
        release_tuple,
        oracle_release_tuple,
        tolerate=lambda product, oracle_value: (
            oracle_value is None
            and product is not None
            and (product.instant - instant) > timedelta(days=400)
        ),
    ):
        differences.append(
            (
                "next_release_after",
                {"t": instant.isoformat()},
                _shown(found, release_tuple),
                _shown(expected, oracle_release_tuple),
            )
        )

    inclusive = rng.random() < 0.5
    found = _product_outcome(
        lambda: next_release_after(rule, instant, calendar, inclusive=inclusive, until=end)
    )
    expected = _oracle_outcome(
        lambda: oracle.o_first_after(
            rule, oracle_calendar, instant if inclusive else instant + US, end
        ),
        oracle,
    )
    if not _agree(found, expected, release_tuple, oracle_release_tuple):
        differences.append(
            (
                "next_release_after_until",
                {"t": instant.isoformat(), "until": end.isoformat(), "inclusive": inclusive},
                _shown(found, release_tuple),
                _shown(expected, oracle_release_tuple),
            )
        )

    found = _product_outcome(lambda: previous_release_at_or_before(rule, instant, calendar))
    expected = _oracle_outcome(
        lambda: oracle.o_last_before(rule, oracle_calendar, instant - timedelta(days=400), instant),
        oracle,
    )
    if not _agree(
        found,
        expected,
        release_tuple,
        oracle_release_tuple,
        tolerate=lambda product, oracle_value: (
            oracle_value is None
            and product is not None
            and (instant - product.instant) > timedelta(days=400)
        ),
    ):
        differences.append(
            (
                "previous_release_at_or_before",
                {"t": instant.isoformat()},
                _shown(found, release_tuple),
                _shown(expected, oracle_release_tuple),
            )
        )

    # The oracle searches a bounded look-around for the verdict context (400 days by
    # default); a dense schedule would enumerate tens of thousands of nominals per case, so
    # dense rules get a 30-day look-around instead and the comparison falls back to the
    # `*_known` flags the oracle sets when it could not look far enough.
    look = timedelta(days=30) if is_dense(rule) else timedelta(days=400)
    for _ in range(2):
        now = instant
        age = timedelta(days=2) if is_dense(rule) else timedelta(days=90)
        choice = rng.random()
        observed = None
        if choice >= 0.15:
            observed = now - timedelta(seconds=rng.randrange(0, int(age.total_seconds())))
            if choice > 0.6:
                near = oracle.o_releases(rule, oracle_calendar, now - age, now)
                if near:
                    release = rng.choice(near).instant
                    observed = rng.choice(
                        [release, release - US, release + US, release - timedelta(minutes=1)]
                    )
        result_outcome = _product_outcome(
            partial(evaluate, rule, RawObservation(observed), now, provider)
        )
        expected_outcome = _oracle_outcome(
            partial(oracle.o_evaluate, rule, observed, now, provider, look=look),
            oracle,
        )
        if result_outcome.refused or expected_outcome.refused:
            if not _agree(result_outcome, expected_outcome):
                differences.append(
                    (
                        "evaluate",
                        {"now": now.isoformat(), "O": observed and observed.isoformat()},
                        _shown(result_outcome, lambda value: value),
                        _shown(expected_outcome, lambda value: value),
                    )
                )
            continue
        result = result_outcome.value
        expected_result = expected_outcome.value
        got_value = [
            result.status.value,
            result.release and result.release.instant.isoformat(),
            result.deadline and result.deadline.isoformat(),
            result.next_expected_arrival and result.next_expected_arrival.isoformat(),
            result.missed_count,
            result.pending_count,
            result.missed_truncated,
            result.error and result.error.code,
        ]
        want_value = [
            expected_result.status,
            expected_result.release and expected_result.release.isoformat(),
            expected_result.deadline and expected_result.deadline.isoformat(),
            expected_result.next and expected_result.next.isoformat(),
            expected_result.missed,
            expected_result.pending,
            expected_result.truncated,
            expected_result.error,
        ]
        if not expected_result.next_known and got_value[3] is not None:
            want_value[3] = got_value[3]
        if (
            not expected_result.next_known
            and not expected_result.last_known
            and got_value[7] == "E209"
            and want_value[7] is None
        ):
            # The oracle looks 400 days around `now`; the implementation looks 1830. When the
            # oracle could see no release in either direction it reports an empty ON_TIME and
            # declares the answer unknown (`*_known` False), so the implementation's stricter
            # "no release at all" (E209) is not a contradiction — it is the more informed
            # statement. The E209 behaviour itself is pinned by the recorded regressions.
            continue
        if (
            not expected_result.last_known
            and expected_result.status in ("ON_TIME", "NO_DATA")
            and expected_result.release is None
        ):
            want_value[1], want_value[2] = got_value[1], got_value[2]
        if got_value != want_value:
            differences.append(
                (
                    "evaluate",
                    {"now": now.isoformat(), "O": observed and observed.isoformat()},
                    got_value,
                    want_value,
                )
            )
        elif result.release is not None and expected_result.release_meta is not None:
            got_meta = release_tuple(result.release)[1:]  # type: ignore[index]
            want_meta = oracle_release_tuple(expected_result.release_meta)[1:]  # type: ignore[index]
            if got_meta != want_meta:
                differences.append(
                    (
                        "evaluate_release_meta",
                        {"now": now.isoformat(), "O": observed and observed.isoformat()},
                        got_meta,
                        want_meta,
                    )
                )
    return differences
