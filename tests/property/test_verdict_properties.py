"""Property tests for the verdict engine (P1-P9, P17; BLUEPRINT.md §9.3).

The invariants are the ones a reader can state without knowing the implementation:
next expected arrivals are in the future, results do not depend on how an instant is
written, more data or more grace or more time can never make a source *more* late, the
decision table's conditions really hold in the reported results, and ON_TIME is only
concluded from a complete search.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import assume, given, settings
from hypothesis import strategies as st
from tests.conftest import FakeCalendarProvider
from tests.property.strategies import verdict_inputs

from freshcal.core.calendar import BusinessCalendar
from freshcal.core.errors import ConfigError
from freshcal.core.model import RawObservation, SourceRule, Status
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
@settings(max_examples=50)
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
        ["Europe/Berlin", "Pacific/Kiritimati", "America/Adak", "Asia/Kathmandu", "UTC"]
    ),
)
@settings(max_examples=50)
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
@settings(max_examples=50)
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
@settings(max_examples=50)
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
@settings(max_examples=50)
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
@settings(max_examples=50)
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
@settings(max_examples=50)
def test_p7_deadline_minus_release_equals_grace(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
) -> None:
    rule, _, provider, now, raw = inputs
    result = evaluate_inputs(rule, raw, now, provider)
    if result.release is not None and result.deadline is not None:
        assert result.deadline - result.release.instant == rule.grace


@given(verdict_inputs(), st.integers(min_value=0, max_value=48 * 60))
@settings(max_examples=50)
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
@settings(max_examples=50)
def test_p9_evaluation_is_deterministic(
    inputs: tuple[SourceRule, object, FakeCalendarProvider, datetime, RawObservation],
) -> None:
    rule, _, provider, now, raw = inputs
    first = evaluate_inputs(rule, raw, now, provider)
    second = evaluate_inputs(rule, raw, now, provider)
    assert first == second


@given(verdict_inputs(), st.integers(min_value=1, max_value=60))
@settings(max_examples=50)
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
        assert direct, "a found release must also be visible to direct enumeration"
