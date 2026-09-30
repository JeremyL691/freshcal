"""Hypothesis strategies for the schedule and verdict property tests (§9.3).

The time-zone list is the one the blueprint names, including the ones whose DST
transitions are unusual (a 30-minute shift for ``Australia/Lord_Howe``, a 45-minute
offset for ``Asia/Kathmandu``, midnight for ``America/Santiago``). Schedules come from
a deliberately small grammar so that failures are explainable, and calendars come from
``FakeCalendarProvider`` so a test states its own holidays.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, _zoneinfo

import hypothesis.strategies as st
from tests.conftest import FakeCalendarProvider

from freshcal.core.calendar import BusinessCalendar
from freshcal.core.errors import CalendarError, ConfigError
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    CronSchedule,
    FreshnessTarget,
    HolidayCalendarRef,
    MonthlyBusinessDaySchedule,
    NonBusinessDayPolicy,
    Origin,
    RawObservation,
    SourceRule,
    Weekday,
)
from freshcal.core.schedule import releases_between

TIMEZONES: tuple[ZoneInfo, ...] = (
    ZoneInfo("UTC"),
    ZoneInfo("Europe/Berlin"),
    ZoneInfo("America/New_York"),
    ZoneInfo("America/Santiago"),
    ZoneInfo("Australia/Lord_Howe"),
    ZoneInfo("Asia/Kathmandu"),
    ZoneInfo("Pacific/Chatham"),
    ZoneInfo("Asia/Shanghai"),
    ZoneInfo("Pacific/Kiritimati"),
    ZoneInfo("America/Phoenix"),  # no DST at all
    ZoneInfo("Asia/Kolkata"),  # a fixed +05:30 offset
    ZoneInfo("Pacific/Apia"),  # a 24 h forward jump (2011-12-30)
    ZoneInfo("Antarctica/Casey"),  # an 8 h forward jump (1969) and a 3 h one (2018)
)

HOLIDAY_REFS: tuple[HolidayCalendarRef, ...] = (
    HolidayCalendarRef("financial", "XECB"),
    HolidayCalendarRef("country", "DE"),
    HolidayCalendarRef("country", "US"),
)

START_INSTANT = datetime(2001, 1, 1, tzinfo=UTC)
END_INSTANT = datetime(2090, 12, 31, 23, 59, tzinfo=UTC)

CRON_MINUTES = ("0", "15", "30", "45", "*/30")
#: §9.3 asks for `0-23|*/6`; the single hours are kept as well so sparse
#: schedules (a few fires a year, the cheapest way to reach the horizon paths) stay in
#: the mix. TEST-04 found the old list had neither `0-23` nor the fixed-offset zones.
CRON_HOURS = ("0-23", "*/6", "0", "6", "12", "18")
CRON_DAYS_OF_MONTH = ("*", "1", "15", "L", "29", "30", "31")
#: T-8.2 adds the nth-weekday syntax (`N#O`) the platform promises, including a fifth
#: occurrence (absent in some months) and a comma alternative. The dense families are listed
#: twice so the sparser `#` schedules stay a minority: drawn evenly they pushed the
#: TEST-04 distribution probe just below its 10 % NOT_DUE floor (measured 9.0-13.8 % with
#: the flat list, 12.3-14.0 % weighted, `#` still ~19 % of drawn rules).
CRON_DAYS_OF_WEEK = (
    "*",
    "1-5",
    "0,6",
    "*",
    "1-5",
    "0,6",
    "1#1",
    "5#5",
    "1#1,1#2",
    "0#3",
)
SUB_HOURLY_MINUTES = ("*/30",)


def instants() -> st.SearchStrategy[datetime]:
    """Aware UTC instants between 2001-01-01 and 2090-12-31."""
    return st.datetimes(
        min_value=START_INSTANT.replace(tzinfo=None),
        max_value=END_INSTANT.replace(tzinfo=None),
    ).map(lambda naive: naive.replace(tzinfo=UTC))


def timezones() -> st.SearchStrategy[ZoneInfo]:
    return st.sampled_from(TIMEZONES)


@lru_cache(maxsize=64)
def zone_transitions(key: str) -> tuple[datetime, ...]:
    """The zone's UTC-offset changes between 2001 and 2090, from its explicit TZif table.

    The audit (TEST-04) found that no property example ever produced a release annotated
    with a DST transition: uniformly drawn instants almost never land inside a one-hour gap
    or overlap. These are the instants the strategies bias towards, read from the same
    table `tests/oracle/cases.py` uses.
    """
    zone = _zoneinfo.ZoneInfo.no_cache(key)
    previous = zone._tti_before.utcoff if zone._tti_before else None
    out: list[datetime] = []
    for timestamp, info in zip(zone._trans_utc, zone._ttinfos, strict=False):
        if previous is not None and info.utcoff != previous:
            moment = datetime.fromtimestamp(timestamp, UTC)
            if 2001 <= moment.year <= 2090:
                out.append(moment)
        previous = info.utcoff
    return tuple(out)


def instants_near_transitions(zone: ZoneInfo) -> st.SearchStrategy[datetime]:
    """Instants on, just before, and just after a real offset change of ``zone``."""
    moments = zone_transitions(zone.key)
    if not moments:
        return instants()
    return st.one_of(
        st.sampled_from(moments),
        st.sampled_from(moments).map(lambda moment: moment - timedelta(minutes=30)),
        st.sampled_from(moments).map(lambda moment: moment + timedelta(minutes=30)),
        st.sampled_from(moments).map(lambda moment: moment + timedelta(hours=3)),
    )


def instants_for(zone: ZoneInfo) -> st.SearchStrategy[datetime]:
    """Instants for a rule in ``zone``: uniform, or near one of its transitions (half)."""
    return st.one_of(
        instants(),
        instants_near_transitions(zone),
    )


def cron_schedules(*, sub_hourly: bool = True) -> st.SearchStrategy[CronSchedule]:
    """Cron schedules from the small grammar of §9.3."""
    minutes = (
        CRON_MINUTES
        if sub_hourly
        else tuple(value for value in CRON_MINUTES if value not in SUB_HOURLY_MINUTES)
    )
    return st.builds(
        _cron_schedule,
        st.sampled_from(minutes),
        st.sampled_from(CRON_HOURS),
        st.sampled_from(CRON_DAYS_OF_MONTH),
        st.sampled_from(CRON_DAYS_OF_WEEK),
        timezones(),
        st.sampled_from(list(NonBusinessDayPolicy)),
    )


def _cron_schedule(
    minute: str,
    hour: str,
    day_of_month: str,
    day_of_week: str,
    timezone: ZoneInfo,
    policy: NonBusinessDayPolicy,
) -> CronSchedule:
    return CronSchedule(
        expression=f"{minute} {hour} {day_of_month} * {day_of_week}",
        timezone=timezone,
        on_non_business_day=policy,
    )


def business_day_schedules() -> st.SearchStrategy[BusinessDaysSchedule]:
    return st.builds(BusinessDaysSchedule, st.times(), timezones())


def monthly_schedules() -> st.SearchStrategy[MonthlyBusinessDaySchedule]:
    numbers = [value for value in range(-23, 24) if value != 0]
    return st.builds(MonthlyBusinessDaySchedule, st.sampled_from(numbers), st.times(), timezones())


def schedules(
    *, sub_hourly: bool = True
) -> st.SearchStrategy[CronSchedule | BusinessDaysSchedule | MonthlyBusinessDaySchedule]:
    options: list[st.SearchStrategy[object]] = [
        cron_schedules(sub_hourly=sub_hourly),
        business_day_schedules(),
        monthly_schedules(),
    ]
    return st.one_of(options)  # type: ignore[return-value]


def calendars(
    reference_year: int = 2026, spread: int = 0
) -> st.SearchStrategy[tuple[CalendarSpec, FakeCalendarProvider]]:
    """A calendar spec plus a provider whose holidays are drawn around ``reference_year``.

    ``spread`` widens the years that get holiday data: the properties that draw ``now``
    themselves pass a spread so the data covers the instants they draw (TEST-04: the old
    strategy had holidays for 2024-2030 only while ``now`` spans 2001-2090).
    """

    @st.composite
    def build(draw: st.DrawFn) -> tuple[CalendarSpec, FakeCalendarProvider]:
        weekend = frozenset(draw(st.sets(st.sampled_from(list(Weekday)), max_size=6)))
        ref = draw(st.sampled_from(HOLIDAY_REFS))
        holiday_dates: dict[tuple[str, int], dict[date, str]] = {}
        for year in range(reference_year - spread, reference_year + spread + 1):
            days = draw(
                st.sets(
                    st.dates(min_value=date(year, 1, 1), max_value=date(year, 12, 31)),
                    max_size=4,
                )
            )
            holiday_dates[(ref.label(), year)] = dict.fromkeys(days, "Test holiday")
        working = frozenset(
            draw(
                st.sets(
                    st.dates(
                        min_value=date(reference_year, 1, 1),
                        max_value=date(reference_year, 12, 31),
                    ),
                    max_size=3,
                )
            )
        )
        non_working = (
            frozenset(
                draw(
                    st.sets(
                        st.dates(
                            min_value=date(reference_year, 1, 1),
                            max_value=date(reference_year, 12, 31),
                        ),
                        max_size=3,
                    )
                )
            )
            - working
        )
        spec = CalendarSpec(
            weekend=weekend,
            holiday_calendars=(ref,),
            extra_working_days=working,
            extra_non_working_days=non_working,
        )
        return spec, FakeCalendarProvider(holiday_dates)

    return build()


def rules(
    *, sub_hourly: bool = True, schedule_strategy: st.SearchStrategy | None = None
) -> st.SearchStrategy[tuple[SourceRule, CalendarSpec, FakeCalendarProvider]]:
    """A rule together with the calendar objects its schedule must be evaluated with.

    ``schedule_strategy`` narrows the kind (P16 draws monthly schedules directly instead of
    filtering them out of the mix, which Hypothesis rightly flags as too much filtering).
    """

    @st.composite
    def build(draw: st.DrawFn) -> tuple[SourceRule, CalendarSpec, FakeCalendarProvider]:
        schedule = draw(
            schedule_strategy if schedule_strategy is not None else schedules(sub_hourly=sub_hourly)
        )
        # A reference instant drives the calendar's holiday years and the `active_from`
        # floor, so a drawn floor is near the data the calendar actually has (TEST-04).
        reference = draw(instants_for(schedule.timezone))
        spec, provider = draw(calendars(reference_year=reference.year, spread=1))
        active_from = draw(
            st.one_of(
                st.none(),
                st.dates(min_value=date(2001, 1, 1), max_value=date(2089, 12, 31)),
                st.just(reference.date()),
                st.just(reference.date() - timedelta(days=draw(st.integers(1, 40)))),
                st.just(reference.date() + timedelta(days=draw(st.integers(1, 40)))),
            )
        )
        rule = SourceRule(
            source_id="property.source",
            origin=Origin.CONFIG,
            schedule=schedule,
            calendar=spec,
            grace=timedelta(minutes=draw(st.integers(min_value=0, max_value=1440))),
            target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
            active_from=active_from,
        )
        return rule, spec, provider

    return build()


def windows(max_days: int = 45) -> st.SearchStrategy[tuple[datetime, datetime]]:
    """``(start, end)`` pairs whose span is at most ``max_days``."""

    @st.composite
    def build(draw: st.DrawFn) -> tuple[datetime, datetime]:
        start = draw(instants())
        length = draw(
            st.timedeltas(
                min_value=timedelta(hours=1),
                max_value=timedelta(days=max_days),
            )
        )
        return start, start + length

    return build()


def graces() -> st.SearchStrategy[timedelta]:
    """Grace windows of §9.3: 0 to 10 days, minute granularity."""
    return st.integers(min_value=0, max_value=10 * 24 * 60).map(
        lambda minutes: timedelta(minutes=minutes)
    )


def observed_offsets(max_days: int = 400) -> st.SearchStrategy[timedelta]:
    """``observed = now + delta`` with delta in [-``max_days``, +2 days] (§9.3).

    §9.3's range is [-400 days, +2 days] and that is what the properties draw (TEST-04
    found the old 90-day bound). The oldest values exercise the horizon paths, which
    fixed tests also pin: U-VER-07, U-VER-11…U-VER-13, G30, G33, G34, PB-2 and PB-3.
    """
    return st.integers(min_value=-max_days * 24 * 60, max_value=2 * 24 * 60).map(
        lambda minutes: timedelta(minutes=minutes)
    )


def is_dense(schedule: object) -> bool:
    """True for cron expressions that fire many times a day (cheap windows only)."""
    if not isinstance(schedule, CronSchedule):
        return False
    minute, hour = schedule.expression.split()[0], schedule.expression.split()[1]
    return hour == "0-23" or minute == "*/30"


@st.composite
def _observation(
    draw: st.DrawFn,
    rule: SourceRule,
    spec: CalendarSpec,
    provider: FakeCalendarProvider,
    now: datetime,
) -> RawObservation:
    """An observed value: one in three sits on a release instant (§9.3 boundaries).

    Releases annotated with a DST transition are preferred when the window has any, because
    TEST-04 found that no property example ever produced one.
    """
    if draw(st.integers(min_value=0, max_value=2)) == 0:
        window = timedelta(days=1) if is_dense(rule.schedule) else timedelta(days=120)
        try:
            calendar = BusinessCalendar(spec, provider, source_id=rule.source_id)
            near = releases_between(rule, now - window, now, calendar)
        except (ConfigError, CalendarError):
            near = []
        pending = [r for r in near if r.instant <= now <= r.instant + rule.grace]
        if pending and draw(st.booleans()):
            # Just before a release that is not due yet: the NOT_DUE boundary, which the
            # audit measured at 1 example in 600 before T-7.3. A DST-annotated release is
            # preferred, so the reported release carries the annotation.
            gap_pending = [release for release in pending if release.dst != "normal"]
            pool = gap_pending or pending
            instant = draw(st.sampled_from([release.instant for release in pool]))
            return RawObservation(
                (
                    instant
                    - draw(st.sampled_from([timedelta(microseconds=1), timedelta(minutes=1)]))
                ).replace(tzinfo=None)
            )
        interesting = [release for release in near if release.dst != "normal"]
        if interesting:
            near = interesting
        if near:
            instant = draw(st.sampled_from([release.instant for release in near]))
            delta = draw(
                st.sampled_from(
                    [
                        timedelta(0),
                        timedelta(microseconds=1),
                        -timedelta(microseconds=1),
                        timedelta(minutes=1),
                        -timedelta(minutes=1),
                        timedelta(minutes=30),
                    ]
                )
            )
            return RawObservation((instant + delta).replace(tzinfo=None))
    offset = draw(st.one_of(st.none(), observed_offsets()))
    return RawObservation(None if offset is None else (now + offset).replace(tzinfo=None))


@st.composite
def verdict_inputs(
    draw: st.DrawFn,
) -> tuple[SourceRule, CalendarSpec, FakeCalendarProvider, datetime, RawObservation]:
    """A rule, its calendar objects, an evaluation instant, and a raw observation.

    Every schedule kind is drawn, sub-hourly cron included (TEST-04: it used to be
    excluded, so no property example ever produced a release with a DST annotation or a
    NOT_DUE verdict). The cost is bounded by the observed age and by the 10 000-release
    counting cap rather than by removing inputs.
    """
    schedule = draw(schedules())
    now = draw(instants_for(schedule.timezone))
    spec, provider = draw(calendars(reference_year=now.year, spread=1))
    grace = draw(graces())
    rule = SourceRule(
        source_id="property.verdict",
        origin=Origin.CONFIG,
        schedule=schedule,
        calendar=spec,
        grace=grace,
        target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
        observed_timezone=ZoneInfo("UTC"),
        active_from=draw(
            st.one_of(
                st.none(),
                st.dates(min_value=date(2001, 1, 1), max_value=date(2089, 12, 31)),
                st.just(now.date()),
                st.just(now.date() - timedelta(days=draw(st.integers(1, 40)))),
                st.just(now.date() + timedelta(days=draw(st.integers(1, 40)))),
            )
        ),
    )
    raw = draw(_observation(rule, spec, provider, now))
    return rule, spec, provider, now, raw


def sample_verdict_inputs(
    rng: random.Random, count: int
) -> list[tuple[SourceRule, CalendarSpec, FakeCalendarProvider, datetime, RawObservation]]:
    """``count`` deterministic verdict inputs drawn with ``rng`` (TEST-04's distribution probe).

    Uses the same choices as :func:`verdict_inputs` but a seeded ``random.Random``, so the
    probe is reproducible: a distribution assertion cannot be a property test (the profile
    owns the example count), and Hypothesis's own ``.example()`` is not seedable.
    """
    out: list[tuple[SourceRule, CalendarSpec, FakeCalendarProvider, datetime, RawObservation]] = []
    for _ in range(count):
        kind = rng.choice(["cron", "cron", "bd", "monthly"])
        zone = rng.choice(TIMEZONES)
        if kind == "cron":
            expression = " ".join(
                [
                    rng.choice(CRON_MINUTES),
                    rng.choice(CRON_HOURS),
                    rng.choice(CRON_DAYS_OF_MONTH),
                    "*",
                    rng.choice(CRON_DAYS_OF_WEEK),
                ]
            )
            schedule: object = CronSchedule(
                expression, zone, rng.choice(list(NonBusinessDayPolicy))
            )
        elif kind == "bd":
            schedule = BusinessDaysSchedule(
                time(rng.randrange(24), rng.choice([0, 15, 30, 45])), zone
            )
        else:
            schedule = MonthlyBusinessDaySchedule(
                rng.choice([v for v in range(-23, 24) if v != 0]),
                time(rng.randrange(24), rng.choice([0, 30])),
                zone,
            )
        moments = zone_transitions(zone.key)
        if moments and rng.randrange(2) == 0:
            now = rng.choice(moments) + timedelta(minutes=rng.choice([-30, 0, 30, 180]))
        else:
            now = START_INSTANT + timedelta(
                minutes=rng.randrange(int((END_INSTANT - START_INSTANT).total_seconds() // 60))
            )
        weekend = frozenset(rng.sample(list(Weekday), rng.randrange(0, 5)))
        year = now.year
        ref = rng.choice(HOLIDAY_REFS)
        holiday_dates = {
            (ref.label(), year): {
                date(year, rng.randrange(1, 13), rng.randrange(1, 29)): "Test holiday"
                for _ in range(rng.randrange(0, 3))
            }
        }
        spec = CalendarSpec(weekend=weekend, holiday_calendars=(ref,))
        provider = FakeCalendarProvider(holiday_dates)
        rule = SourceRule(
            source_id="property.probe",
            origin=Origin.CONFIG,
            schedule=schedule,  # type: ignore[arg-type]
            calendar=spec,
            grace=timedelta(minutes=rng.randrange(0, 10 * 24 * 60 + 1)),
            target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
            observed_timezone=ZoneInfo("UTC"),
            active_from=rng.choice(
                [
                    None,
                    now.date(),
                    now.date() - timedelta(days=rng.randrange(1, 41)),
                    now.date() + timedelta(days=rng.randrange(1, 41)),
                ]
            ),
        )
        # Mirrors `_observation`: one in five sits on a release instant, half of the rest
        # have no observation at all, the remainder is a uniform age up to 400 days.
        raw = None
        if rng.randrange(3) == 0:
            window = timedelta(days=1) if is_dense(schedule) else timedelta(days=120)
            try:
                near = releases_between(rule, now - window, now, BusinessCalendar(spec, provider))
            except (ConfigError, CalendarError):
                near = []
            pending = [r for r in near if r.instant <= now <= r.instant + rule.grace]
            if pending and rng.random() < 0.5:
                gap_pending = [release for release in pending if release.dst != "normal"]
                pool = gap_pending or pending
                instant = rng.choice([release.instant for release in pool])
                delta = rng.choice([timedelta(microseconds=1), timedelta(minutes=1)])
                raw = RawObservation((instant - delta).replace(tzinfo=None))
            else:
                interesting = [release for release in near if release.dst != "normal"]
                if interesting:
                    near = interesting
                if near:
                    instant = rng.choice([release.instant for release in near])
                    delta = rng.choice(
                        [
                            timedelta(0),
                            timedelta(microseconds=1),
                            -timedelta(microseconds=1),
                            timedelta(minutes=1),
                            -timedelta(minutes=1),
                            timedelta(minutes=30),
                        ]
                    )
                    raw = RawObservation((instant + delta).replace(tzinfo=None))
        if raw is None:
            if rng.random() < 0.5:
                raw = RawObservation(None)
            else:
                offset = rng.randrange(-400 * 24 * 60, 2 * 24 * 60 + 1)
                raw = RawObservation((now + timedelta(minutes=offset)).replace(tzinfo=None))
        out.append((rule, spec, provider, now, raw))
    return out
