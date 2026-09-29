"""Property tests for the verdict engine (P1-P9, P17; BLUEPRINT.md §9.3).

The invariants are the ones a reader can state without knowing the implementation:
next expected arrivals are in the future, results do not depend on how an instant is
written, more data or more grace or more time can never make a source *more* late, the
decision table's conditions really hold in the reported results, and ON_TIME is only
concluded from a complete search.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import assume, given
from hypothesis import strategies as st
from tests.conftest import FakeCalendarProvider
from tests.property.strategies import rules, verdict_inputs, windows

from freshcal.core.calendar import BusinessCalendar
from freshcal.core.errors import ConfigError
from freshcal.core.model import (
    CalendarSpec,
    CronSchedule,
    FreshnessTarget,
    MonthlyBusinessDaySchedule,
    NonBusinessDayPolicy,
    Origin,
    RawObservation,
    SourceRule,
    Status,
    Weekday,
)
from freshcal.core.schedule import releases_between
from freshcal.core.timeutil import resolve_local
from freshcal.core.verdict import evaluate, find_first_unarrived

SEVERITY = {Status.ON_TIME: 0, Status.NOT_DUE: 1, Status.OVERDUE: 2}
MICROSECOND = timedelta(microseconds=1)


def severity(status: Status) -> int | None:
    return SEVERITY.get(status)


def evaluate_inputs(
    rule: SourceRule, raw: RawObservation, now: datetime, provider: FakeCalendarProvider
):
    return evaluate(rule, raw, now, provider)


@given(verdict_inputs())
def test_p1_next_expected_arrival_is_in_the_future(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
) -> None:
    rule, _, provider, now, raw = inputs
    result = evaluate_inputs(rule, raw, now, provider)
    if result.next_expected_arrival is not None:
        assert result.next_expected_arrival > now


@given(
    verdict_inputs(),
    st.sampled_from(
        [
            "Europe/Berlin",
            "Pacific/Kiritimati",
            "America/Adak",
            "Asia/Kathmandu",
            "UTC",
            "America/Phoenix",  # a fixed offset with no DST
            "Asia/Kolkata",  # a fixed +05:30 offset
        ]
    ),
)
def test_p2_results_do_not_depend_on_the_representation_of_instants(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
    timezone_name: str,
) -> None:
    rule, _, provider, now, raw = inputs
    assume(raw.value is not None)  # the strategy draws naive values, interpreted in UTC
    other_zone = ZoneInfo(timezone_name)
    aware_raw = RawObservation(raw.value.replace(tzinfo=UTC))

    baseline = evaluate_inputs(rule, aware_raw, now, provider)
    shifted_raw = RawObservation(aware_raw.value.astimezone(other_zone))  # type: ignore[union-attr]
    shifted_now = now.astimezone(other_zone)
    shifted = evaluate_inputs(rule, shifted_raw, shifted_now, provider)

    assert shifted.status is baseline.status
    assert shifted.release == baseline.release
    assert shifted.deadline == baseline.deadline
    assert shifted.next_expected_arrival == baseline.next_expected_arrival
    assert shifted.explanation == baseline.explanation


@given(verdict_inputs(), st.integers(min_value=1, max_value=30 * 24 * 60))
def test_p3_monotone_in_observed(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
    later_minutes: int,
) -> None:
    rule, _, provider, now, raw = inputs
    assume(raw.value is not None)
    earlier = raw.value
    later = earlier + timedelta(minutes=later_minutes)

    result_earlier = evaluate_inputs(rule, RawObservation(earlier), now, provider)
    result_later = evaluate_inputs(rule, RawObservation(later), now, provider)
    assume(severity(result_earlier.status) is not None)
    assume(severity(result_later.status) is not None)
    assert severity(result_later.status) <= severity(result_earlier.status)


@given(verdict_inputs(), st.integers(min_value=1, max_value=7 * 24 * 60))
def test_p4_monotone_in_grace(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
    extra_minutes: int,
) -> None:
    rule, _, provider, now, raw = inputs
    tighter = rule
    looser = SourceRule(
        source_id=rule.source_id,
        origin=rule.origin,
        schedule=rule.schedule,
        calendar=rule.calendar,
        grace=rule.grace + timedelta(minutes=extra_minutes),
        target=rule.target,
        observed_timezone=rule.observed_timezone,
        active_from=rule.active_from,
    )
    result_tighter = evaluate_inputs(tighter, raw, now, provider)
    result_looser = evaluate_inputs(looser, raw, now, provider)
    assume(severity(result_tighter.status) is not None)
    assume(severity(result_looser.status) is not None)
    assert severity(result_looser.status) <= severity(result_tighter.status)


@given(verdict_inputs(), st.integers(min_value=1, max_value=30 * 24 * 60))
def test_p5_monotone_in_time(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
    later_minutes: int,
) -> None:
    rule, _, provider, now, raw = inputs
    assume(raw.value is not None)
    result_earlier = evaluate_inputs(rule, raw, now, provider)
    result_later = evaluate_inputs(rule, raw, now + timedelta(minutes=later_minutes), provider)
    assume(severity(result_earlier.status) is not None)
    assume(severity(result_later.status) is not None)
    # Releases only accumulate and deadlines only pass, so a later instant is never
    # *less* late than an earlier one.
    assert severity(result_later.status) >= severity(result_earlier.status)


@given(verdict_inputs())
def test_p6_status_consistency(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
) -> None:
    rule, _, provider, now, raw = inputs
    result = evaluate_inputs(rule, raw, now, provider)

    # The instant an unarrived release must be later than: the observed timestamp, or the
    # `active_from` floor when the table is empty (§3.8.10). The floor case is inclusive
    # (the release at the floor counts), the observation case strict.
    if result.observation is not None:
        reference = result.observation.instant
        later = lambda release: release > reference  # noqa: E731
    elif rule.active_from is not None:
        reference = resolve_local(
            datetime.combine(rule.active_from, time.min), rule.schedule.timezone
        )
        later = lambda release: release >= reference  # noqa: E731
    else:
        # An empty table without a floor has no releases to be later than.
        assert result.status is Status.NO_DATA, result.status
        return

    if result.status is Status.ON_TIME:
        if result.release is not None:
            assert result.release.instant <= now
            assert result.release.instant <= reference
    elif result.status is Status.NOT_DUE:
        assert result.release is not None
        assert result.deadline is not None
        assert later(result.release.instant)
        assert result.release.instant <= now <= result.deadline
        assert result.pending_count >= 1
    elif result.status is Status.OVERDUE:
        assert result.release is not None
        assert result.deadline is not None
        assert later(result.release.instant)
        assert result.deadline < now
        assert result.missed_count >= 1


@given(verdict_inputs())
def test_p7_deadline_minus_release_equals_grace(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
) -> None:
    rule, _, provider, now, raw = inputs
    result = evaluate_inputs(rule, raw, now, provider)
    if result.release is not None and result.deadline is not None:
        assert result.deadline - result.release.instant == rule.grace


@given(verdict_inputs(), st.integers(min_value=0, max_value=48 * 60))
def test_p8_observed_at_or_after_now_is_on_time(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
    future_minutes: int,
) -> None:
    rule, _, provider, now, raw = inputs
    assume(raw.value is not None)
    future = now + timedelta(minutes=future_minutes)
    result = evaluate_inputs(rule, RawObservation(future.replace(tzinfo=None)), now, provider)
    if severity(result.status) is None:
        return  # E209/E215: the rule itself cannot be evaluated
    assert result.status is Status.ON_TIME


@given(verdict_inputs())
def test_p9_evaluation_is_deterministic(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
) -> None:
    rule, _, provider, now, raw = inputs
    first = evaluate_inputs(rule, raw, now, provider)
    second = evaluate_inputs(rule, raw, now, provider)
    assert first == second


@given(verdict_inputs(), st.integers(min_value=1, max_value=60))
def test_p17_find_first_unarrived_only_returns_none_after_a_complete_search(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
    window_days: int,
) -> None:
    """ON_TIME must be proven: `None` only when a direct enumeration finds nothing."""
    rule, spec, provider, now, _ = inputs
    calendar = BusinessCalendar(spec, provider, source_id=rule.source_id)  # type: ignore[arg-type]
    start = now - timedelta(days=window_days)
    try:
        found, _ = find_first_unarrived(rule, start, False, now, calendar)
    except ConfigError:
        assume(False)
        return
    direct = releases_between(rule, start + MICROSECOND, now, calendar)
    if found is None:
        assert direct == [], (rule.schedule, start, now, direct[:3])
    else:
        # P17 (TEST-08): not just "some release exists" — the *same* first release, which is
        # what "the search agrees with direct enumeration" means.
        assert direct, "a found release must also be visible to direct enumeration"
        assert found == direct[0], (rule.schedule, start, now, found, direct[0])


@given(rules(), windows(max_days=30))
def test_p18_searches_and_verdicts_match_the_oracle(
    rule_and_calendar: tuple[SourceRule, object, FakeCalendarProvider],
    window: tuple[datetime, datetime],
) -> None:
    """P18 (rule P1): the drawn inputs are answered exactly as the independent oracle does.

    This is the property form of `tests/oracle/test_differential.py`: the same strategies
    the other properties use, compared against the brute-force reference for
    `releases_between`, `next_release_after` (strict and bounded), `previous_release_at_or_before`
    and `evaluate`.
    """
    import random

    from tests.oracle import oracle
    from tests.oracle.cases import compare_case

    rule, _spec, provider = rule_and_calendar
    start, _end = window
    disagreements = compare_case(rule, provider, start, random.Random(0), oracle)
    assert disagreements == [], disagreements[:2]


def test_p18_recorded_counterexamples_stay_fixed() -> None:
    """The inputs that once broke a property are pinned as fixed cases (rule P7).

    T-2.5's two counterexamples: P13 must not require business-day dates for cron with
    policy `none` (the calendar is never consulted), and P16 must not require a month that
    a window starting before `active_from` legitimately drops. G41-G44's inputs are pinned
    by the golden rows themselves.
    """
    zone = ZoneInfo("UTC")
    weekend_rule = SourceRule(
        source_id="property.counterexample",
        origin=Origin.CONFIG,
        schedule=CronSchedule("0 12 * * 6", zone, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(weekend=frozenset({Weekday.SAT, Weekday.SUN})),
        grace=timedelta(hours=1),
        target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
    )
    calendar = BusinessCalendar(weekend_rule.calendar, FakeCalendarProvider())
    releases = releases_between(
        weekend_rule,
        datetime(2026, 1, 3, tzinfo=UTC),
        datetime(2026, 1, 4, tzinfo=UTC),
        calendar,
    )
    assert [release.instant for release in releases] == [datetime(2026, 1, 3, 12, tzinfo=UTC)]
    assert not calendar.is_business_day(date(2026, 1, 3))

    floor_rule = SourceRule(
        source_id="property.counterexample",
        origin=Origin.CONFIG,
        schedule=MonthlyBusinessDaySchedule(1, time(9, 0), zone),
        calendar=CalendarSpec(),
        grace=timedelta(hours=1),
        target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
        active_from=date(2026, 2, 10),
    )
    releases = releases_between(
        floor_rule,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 3, 31, tzinfo=UTC),
        calendar,
    )
    assert [release.local.date().isoformat() for release in releases] == ["2026-03-02"]


def test_verdict_input_distribution_is_wide_enough() -> None:
    """TEST-04 probe: the widened strategies really reach the states the audit found missing.

    A seeded draw of 600 verdict inputs (the same choices the strategies make) must produce
    at least 10 % NOT_DUE verdicts - the audit measured 1/600 before T-7.3 - and at least one
    release whose DST classification is not `normal` (0/600 before).
    """
    import random
    from collections import Counter

    from tests.property.strategies import sample_verdict_inputs

    statuses: Counter[str] = Counter()
    annotated = 0
    for rule, _spec, provider, now, raw in sample_verdict_inputs(random.Random(20260929), 600):
        result = evaluate(rule, raw, now, provider)
        statuses[result.status.value] += 1
        if result.release is not None and result.release.dst != "normal":
            annotated += 1
    assert statuses["NOT_DUE"] >= 60, statuses
    assert annotated >= 1, statuses

    # The drawn sample reaches a DST-annotated release about once in 300 cases, so the two
    # golden rules that pin the gap class (G42, G44) are checked here as well: the probe
    # must show DST coverage deterministically, not by luck.
    provider = FakeCalendarProvider()
    gap_rules = (
        (
            CronSchedule("30 2 * * *", ZoneInfo("Europe/Berlin")),
            datetime(2027, 3, 28, 3, tzinfo=UTC),
        ),
        (
            CronSchedule("10,30 2 * * *", ZoneInfo("Australia/Lord_Howe")),
            datetime(2026, 10, 3, 16, 30, tzinfo=UTC),
        ),
    )
    for schedule, now in gap_rules:
        rule = SourceRule(
            source_id="property.gap",
            origin=Origin.CONFIG,
            schedule=schedule,
            calendar=CalendarSpec(),
            grace=timedelta(hours=1),
            target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
        )
        result = evaluate(rule, RawObservation(None), now, provider)
        assert result.release is not None
        assert result.release.dst == "gap", (schedule, result.release)
