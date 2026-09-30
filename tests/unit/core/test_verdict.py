"""Tests for the verdict engine (BLUEPRINT.md §3.7)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from tests.conftest import FakeCalendarProvider

from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    CronSchedule,
    FreshnessTarget,
    HolidayCalendarRef,
    NonBusinessDayPolicy,
    Origin,
    RawObservation,
    SourceRule,
    Status,
)
from freshcal.core.schedule import MAX_COUNTED_RELEASES, SEARCH_HORIZON
from freshcal.core.verdict import evaluate, schedule_context

BERLIN = ZoneInfo("Europe/Berlin")
UTC_ZONE = ZoneInfo("UTC")
XECB = HolidayCalendarRef("financial", "XECB")

# ECB-like rule: business days at 16:00 Berlin, 2h grace, TARGET holidays.
ECB_HOLIDAYS = {
    ("financial XECB", 2026): {
        date(2026, 1, 1): "New Year's Day",
        date(2026, 4, 3): "Good Friday",
        date(2026, 4, 6): "Easter Monday",
        date(2026, 5, 1): "Labour Day",
        date(2026, 12, 25): "Christmas Day",
        date(2026, 12, 26): "Christmas Holiday",
    }
}


def ecb_rule(
    *,
    grace: timedelta = timedelta(hours=2),
    active_from: date | None = None,
    observed_timezone: ZoneInfo | None = UTC_ZONE,
    schedule: object | None = None,
) -> SourceRule:
    return SourceRule(
        source_id="ecb.fx_rates",
        origin=Origin.CONFIG,
        schedule=schedule or BusinessDaysSchedule(at=time(16, 0), timezone=BERLIN),  # type: ignore[arg-type]
        calendar=CalendarSpec(holiday_calendars=(XECB,)),
        grace=grace,
        target=FreshnessTarget(relation="raw.ecb_fx_rates", loaded_at_field="_loaded_at"),
        observed_timezone=observed_timezone,
        active_from=active_from,
    )


def provider(**kwargs: object) -> FakeCalendarProvider:
    return FakeCalendarProvider(ECB_HOLIDAYS, **kwargs)  # type: ignore[arg-type]


def aware(text: str) -> datetime:
    return datetime.fromisoformat(text)


def naive(text: str) -> datetime:
    return datetime.fromisoformat(text)


# Friday 2026-09-25 16:00 CEST = 14:00Z; Monday 2026-09-28 16:00 CEST = 14:00Z.
FRIDAY_RELEASE = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
MONDAY_RELEASE = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)


def test_u_ver_01_decision_table_rows_three_to_six() -> None:
    # Row 3: NULL observation without active_from → NO_DATA, with context.
    result = evaluate(ecb_rule(), RawObservation(None), aware("2026-09-28T05:30:00Z"), provider())
    assert result.status is Status.NO_DATA
    assert result.release is not None
    assert result.release.instant == FRIDAY_RELEASE
    assert result.next_expected_arrival == MONDAY_RELEASE
    assert result.deadline == datetime(2026, 9, 25, 16, 0, tzinfo=UTC)

    # Row 4: ON_TIME (Friday's data present on Monday morning).
    result = evaluate(
        ecb_rule(),
        RawObservation(naive("2026-09-25 14:07:00")),
        aware("2026-09-28T05:30:00Z"),
        provider(),
    )
    assert result.status is Status.ON_TIME
    assert result.release is not None
    assert result.release.instant == FRIDAY_RELEASE
    assert result.missed_count == 0
    assert result.pending_count == 0

    # Row 5: NOT_DUE (inside the grace window of Monday's release).
    result = evaluate(
        ecb_rule(),
        RawObservation(naive("2026-09-25 14:07:00")),
        aware("2026-09-28T14:30:00Z"),
        provider(),
    )
    assert result.status is Status.NOT_DUE
    assert result.release is not None
    assert result.release.instant == MONDAY_RELEASE
    assert result.pending_count == 1
    assert result.missed_count == 0

    # Row 6: OVERDUE (one second past the deadline).
    result = evaluate(
        ecb_rule(),
        RawObservation(naive("2026-09-25 14:07:00")),
        aware("2026-09-28T16:00:01Z"),
        provider(),
    )
    assert result.status is Status.OVERDUE
    assert result.release is not None
    assert result.release.instant == MONDAY_RELEASE
    assert result.missed_count == 1
    assert result.pending_count == 0


def test_u_ver_02_boundaries() -> None:
    rule = ecb_rule()
    # O == R: arrived.
    result = evaluate(
        rule, RawObservation(MONDAY_RELEASE), aware("2026-09-28T17:00:00Z"), provider()
    )
    assert result.status is Status.ON_TIME

    # now == R: occurred, not arrived → pending.
    result = evaluate(
        rule, RawObservation(naive("2026-09-25 14:07:00")), MONDAY_RELEASE, provider()
    )
    assert result.status is Status.NOT_DUE
    assert result.release is not None
    assert result.release.instant == MONDAY_RELEASE

    # now == D: still inside the closed grace window.
    deadline = MONDAY_RELEASE + timedelta(hours=2)
    result = evaluate(rule, RawObservation(naive("2026-09-25 14:07:00")), deadline, provider())
    assert result.status is Status.NOT_DUE

    # now == D + 1 µs: overdue.
    result = evaluate(
        rule,
        RawObservation(naive("2026-09-25 14:07:00")),
        deadline + timedelta(microseconds=1),
        provider(),
    )
    assert result.status is Status.OVERDUE


def test_u_ver_03_several_pending_releases() -> None:
    """Grace larger than the release interval: several releases are pending at once."""
    rule = ecb_rule(grace=timedelta(days=5))
    # Observed Thursday 2026-09-24; now Monday 16:30Z → Thursday and Friday releases
    # have arrived, Monday's has not, and nothing is missed yet.
    result = evaluate(
        rule,
        RawObservation(naive("2026-09-24 14:05:00")),
        aware("2026-09-28T16:30:00Z"),
        provider(),
    )
    assert result.status is Status.NOT_DUE
    assert result.pending_count == 2  # Friday's and Monday's releases
    assert result.release is not None
    # The reported release is the oldest pending one (U), not the most recent.
    assert result.release.instant == FRIDAY_RELEASE

    # With the observed timestamp a week older, two releases are already past their
    # 5-day deadlines (Tuesday's and Wednesday's) and three are still pending.
    result = evaluate(
        rule,
        RawObservation(naive("2026-09-21 14:05:00")),
        aware("2026-09-28T16:30:00Z"),
        provider(),
    )
    assert result.status is Status.OVERDUE
    assert result.missed_count == 2
    assert result.pending_count == 3


def test_u_ver_04_active_from_with_null_observation() -> None:
    rule = ecb_rule(active_from=date(2026, 10, 1))

    # Before the first release (Thursday 2026-10-01 16:00 CEST): nothing due yet.
    result = evaluate(rule, RawObservation(None), aware("2026-09-30T12:00:00Z"), provider())
    assert result.status is Status.ON_TIME
    assert result.release is None
    assert result.deadline is None
    assert result.next_expected_arrival == datetime(2026, 10, 1, 14, 0, tzinfo=UTC)

    # Within the grace window: NOT_DUE.
    result = evaluate(rule, RawObservation(None), aware("2026-10-01T15:00:00Z"), provider())
    assert result.status is Status.NOT_DUE
    assert result.release is not None
    assert result.release.instant == datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
    assert result.pending_count == 1

    # After the deadline: OVERDUE.
    result = evaluate(rule, RawObservation(None), aware("2026-10-01T16:30:00Z"), provider())
    assert result.status is Status.OVERDUE
    assert result.missed_count == 1


def test_u_ver_05_never_firing_schedule_is_config_error() -> None:
    rule = ecb_rule(schedule=CronSchedule("0 0 30 2 *", UTC_ZONE, NonBusinessDayPolicy.NONE))
    result = evaluate(
        rule,
        RawObservation(naive("2026-09-25 14:07:00")),
        aware("2026-09-28T05:30:00Z"),
        provider(),
    )
    assert result.status is Status.CONFIG_ERROR
    assert result.error is not None
    assert result.error.code == "E209"
    assert result.release is None
    assert result.deadline is None
    assert result.next_expected_arrival is None


def test_u_ver_06_provider_out_of_range_is_config_error() -> None:
    rule = ecb_rule()
    result = evaluate(
        rule,
        RawObservation(naive("1990-01-02 14:07:00")),
        aware("1990-01-03T05:30:00Z"),
        provider(start_year=1999, end_year=2100),
    )
    assert result.status is Status.CONFIG_ERROR
    assert result.error is not None
    assert result.error.code == "E405"


def test_u_ver_07_truncated_counting() -> None:
    # A per-minute schedule with data 10 days old: more unarrived releases than the cap.
    rule = SourceRule(
        source_id="vendor.minutely",
        origin=Origin.CONFIG,
        schedule=CronSchedule("* * * * *", UTC_ZONE, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(),
        grace=timedelta(hours=1),
        target=FreshnessTarget(relation="raw.minutely", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    now = aware("2026-09-28T12:00:00Z")
    observed = now - timedelta(days=10)
    result = evaluate(rule, RawObservation(observed.replace(tzinfo=None)), now, provider())
    assert result.status is Status.OVERDUE
    assert result.missed_count == MAX_COUNTED_RELEASES
    assert result.missed_truncated is True
    assert result.pending_count == 0


def test_u_ver_08_grace_across_a_dst_change_is_elapsed_time() -> None:
    """A Saturday 23:00 Berlin release with 4h grace: deadline 02:00 CET, exactly 4h later."""
    rule = SourceRule(
        source_id="vendor.nightly",
        origin=Origin.CONFIG,
        schedule=CronSchedule("0 23 * * *", BERLIN, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(),
        grace=timedelta(hours=4),
        target=FreshnessTarget(relation="raw.nightly", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    # Observed: Friday 2026-10-23 21:05Z, just after that day's 23:00 CEST release.
    # Now: 01:30Z on 2026-10-25 (02:30 CET), past the 01:00Z deadline of Saturday's.
    now = aware("2026-10-25T01:30:00Z")
    result = evaluate(rule, RawObservation(naive("2026-10-23 21:05:00")), now, provider())
    assert result.status is Status.OVERDUE
    assert result.release is not None
    assert result.release.instant == datetime(2026, 10, 24, 21, 0, tzinfo=UTC)  # 23:00 CEST
    assert result.deadline == datetime(2026, 10, 25, 1, 0, tzinfo=UTC)  # 02:00 CET
    assert result.deadline - result.release.instant == timedelta(hours=4)


def test_u_ver_09_naive_now_raises_value_error() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate(ecb_rule(), RawObservation(None), datetime(2026, 9, 28, 5, 30), provider())


def test_u_ver_10_schedule_context_for_the_query_error_path() -> None:
    rule = ecb_rule()
    last, following = schedule_context(rule, aware("2026-09-28T05:30:00Z"), provider())
    assert last is not None
    assert last.instant == FRIDAY_RELEASE
    assert following == MONDAY_RELEASE

    # A never-firing schedule has no context at all: E209.
    broken = ecb_rule(schedule=CronSchedule("0 0 30 2 *", UTC_ZONE, NonBusinessDayPolicy.NONE))
    from freshcal.core.errors import ConfigError

    with pytest.raises(ConfigError) as excinfo:
        schedule_context(broken, aware("2026-09-28T05:30:00Z"), provider())
    assert excinfo.value.issue.code == "E209"


def test_u_ver_11_step_one_proves_overdue_without_old_calendar_data() -> None:
    """Observed 1970-01-01 with a provider that starts in 1999: OVERDUE, truncated, no E405."""
    rule = ecb_rule()
    result = evaluate(
        rule,
        RawObservation(naive("1970-01-01 00:00:00")),
        aware("2026-09-28T12:00:00Z"),
        provider(start_year=1999, end_year=2100),
    )
    assert result.status is Status.OVERDUE
    assert result.missed_truncated is True
    assert result.error is None


def test_u_ver_12_step_two_finds_the_exact_oldest_miss() -> None:
    """G33: a century gap finds the 2096-02-29 release exactly."""
    rule = SourceRule(
        source_id="vendor.leap",
        origin=Origin.CONFIG,
        schedule=CronSchedule("0 0 29 2 *", UTC_ZONE, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(),
        grace=timedelta(days=1),
        target=FreshnessTarget(relation="raw.leap", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    result = evaluate(
        rule,
        RawObservation(naive("2092-03-01 00:00:00")),
        aware("2102-01-01T00:00:00Z"),
        provider(),
    )
    assert result.status is Status.OVERDUE
    assert result.release is not None
    assert result.release.instant == datetime(2096, 2, 29, tzinfo=UTC)
    assert result.deadline == datetime(2096, 3, 1, tzinfo=UTC)
    assert result.missed_truncated is False
    assert result.missed_count == 1


def test_u_ver_13_undecidable_case_is_e215() -> None:
    """G34: a gap longer than the horizon after the observed timestamp is undecidable."""
    rule = SourceRule(
        source_id="vendor.leap",
        origin=Origin.CONFIG,
        schedule=CronSchedule("0 0 29 2 *", UTC_ZONE, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(),
        grace=timedelta(days=1),
        target=FreshnessTarget(relation="raw.leap", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    result = evaluate(
        rule,
        RawObservation(naive("2096-03-01 00:00:00")),
        aware("2103-06-01T00:00:00Z"),
        provider(),
    )
    assert result.status is Status.CONFIG_ERROR
    assert result.error is not None
    assert result.error.code == "E215"
    assert result.release is None

    # The same rule with data 2092-03-01 and now 2097-01-01 is not stale beyond the
    # horizon, so the search is complete and returns a verdict.
    result = evaluate(
        rule,
        RawObservation(naive("2092-03-01 00:00:00")),
        aware("2097-01-01T00:00:00Z"),
        provider(),
    )
    assert result.status is Status.OVERDUE
    assert result.release is not None
    assert result.release.instant == datetime(2096, 2, 29, tzinfo=UTC)


def test_u_ver_14_e214_and_e408_become_config_errors_with_null_context() -> None:
    # E214: a naive value with no configured zone.
    rule = ecb_rule(observed_timezone=None)
    result = evaluate(
        rule,
        RawObservation(naive("2026-09-25 14:07:00")),
        aware("2026-09-28T05:30:00Z"),
        provider(),
    )
    assert result.status is Status.CONFIG_ERROR
    assert result.error is not None
    assert result.error.code == "E214"
    assert result.release is None
    assert result.deadline is None
    assert result.next_expected_arrival is None

    # E408: the calendar's valid_until has passed.
    expired = SourceRule(
        source_id="cn.daily_sales",
        origin=Origin.CONFIG,
        schedule=BusinessDaysSchedule(at=time(15, 0), timezone=ZoneInfo("Asia/Shanghai")),
        calendar=CalendarSpec(name="cn_workdays", valid_until=date(2026, 12, 31)),
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation="raw.cn", loaded_at_field="_loaded_at"),
        observed_timezone=ZoneInfo("Asia/Shanghai"),
    )
    result = evaluate(
        expired,
        RawObservation(naive("2026-12-31 15:05:00")),
        aware("2027-01-04T02:00:00Z"),
        provider(),
    )
    assert result.status is Status.CONFIG_ERROR
    assert result.error is not None
    assert result.error.code == "E408"
    assert result.release is None
    assert result.deadline is None
    assert result.next_expected_arrival is None


def test_w005_when_the_calendar_is_consulted_past_valid_until() -> None:
    rule = SourceRule(
        source_id="cn.daily_sales",
        origin=Origin.CONFIG,
        schedule=BusinessDaysSchedule(at=time(15, 0), timezone=ZoneInfo("Asia/Shanghai")),
        calendar=CalendarSpec(name="cn_workdays", valid_until=date(2026, 12, 31)),
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation="raw.cn", loaded_at_field="_loaded_at"),
        observed_timezone=ZoneInfo("Asia/Shanghai"),
    )
    # Evaluating on 2026-12-30 looks up 2027 dates for the next expected arrival.
    result = evaluate(
        rule,
        RawObservation(naive("2026-12-29 15:05:00")),
        aware("2026-12-30T02:00:00Z"),
        provider(),
    )
    assert result.status in (Status.ON_TIME, Status.NOT_DUE, Status.OVERDUE)
    assert [issue.code for issue in result.warnings] == ["W005"]
    assert result.warnings[0].location == "cn.daily_sales"
    # The chunked search consults every date of its window, so the reported date is the
    # end of the first chunk rather than the next release itself (2027-01-04).
    assert "was consulted for 2027-" in result.warnings[0].message
    assert "after its valid_until 2026-12-31" in result.warnings[0].message


def test_u_ver_15_no_w005_on_a_config_error_result() -> None:
    """SEM-07: W005 belongs to non-error returns only.

    ``review/v0.1.0/A-semantics/repro_w005_on_error.py``: a leap-day schedule whose
    calendar is valid until 2103-12-31 cannot be decided for a 2096 observation (E215),
    and the search for the next arrival consults 2104-02-29 — past ``valid_until``. The
    lookup is real, but a result that carries no verdict must not also carry a warning
    about the inputs of a verdict (§3.7.2: W005 after any non-error return).
    """
    rule = SourceRule(
        source_id="vendor.leap",
        origin=Origin.CONFIG,
        schedule=CronSchedule("0 0 29 2 *", UTC_ZONE, NonBusinessDayPolicy.SKIP),
        calendar=CalendarSpec(name="c", valid_until=date(2103, 12, 31)),
        grace=timedelta(days=1),
        target=FreshnessTarget(relation="raw.leap", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    result = evaluate(
        rule,
        RawObservation(aware("2096-03-01T00:00:00Z")),
        aware("2103-06-01T00:00:00Z"),
        FakeCalendarProvider(),
    )
    assert result.status is Status.CONFIG_ERROR
    assert result.error is not None
    assert result.error.code == "E215"
    assert result.warnings == ()


def test_warnings_and_errors_carry_the_source_id() -> None:
    rule = ecb_rule()
    result = evaluate(
        rule,
        RawObservation(aware("2026-09-28T20:00:00Z")),
        aware("2026-09-28T05:30:00Z"),
        provider(),
    )
    assert [issue.code for issue in result.warnings] == ["W002", "W003"]
    assert {issue.location for issue in result.warnings} == {"ecb.fx_rates"}
    assert result.status is Status.ON_TIME


def leap_rule() -> SourceRule:
    """The G33/v1_demo rule: 29 February only, UTC, one day of grace."""
    return SourceRule(
        source_id="vendor.leap",
        origin=Origin.CONFIG,
        schedule=CronSchedule("0 0 29 2 *", UTC_ZONE, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(),
        grace=timedelta(days=1),
        target=FreshnessTarget(relation="raw.leap", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )


def test_v1_step_one_requires_now_past_the_deadline() -> None:
    """Kills mutant V1: step 1 must not count ``now == deadline`` as missed.

    ``review/v0.1.0/D-process-tests/v1_demo.py``: with data of 2092-03-01 and now exactly
    2104-03-01 the 2104-02-29 release's deadline *is* now (release + 1 day), so the recent
    window proves nothing and step 2 must find the 2096-02-29 miss. The ``>=`` mutant
    returns NOT_DUE for the 2104 release, truncating away the real OVERDUE; the boundary
    is the closed grace window, which must not flip OVERDUE to NOT_DUE.
    """
    result = evaluate(
        leap_rule(),
        RawObservation(naive("2092-03-01 00:00:00")),
        aware("2104-03-01T00:00:00Z"),
        provider(),
    )
    assert result.status is Status.OVERDUE
    assert result.release is not None
    assert result.release.instant == datetime(2096, 2, 29, tzinfo=UTC)
    assert result.missed_count == 1
    assert result.pending_count == 1
    assert result.missed_truncated is False


def test_v5_horizon_start_equality_is_a_complete_search() -> None:
    """Kills mutant V5: an observation exactly at ``now - SEARCH_HORIZON`` is not stale.

    The release at the observed instant has arrived, so the first unarrived release is the
    next day's. The ``>`` mutant sends the equality case through the stale branch, whose
    step 1 treats that already-arrived release as unarrived and returns
    ``missed_truncated=True``; the boundary decides whether the count is a complete one.
    """
    now = datetime(2030, 1, 1, 0, 0, tzinfo=UTC)
    start = now - SEARCH_HORIZON  # 2024-12-28T00:00:00Z, also a release instant
    rule = SourceRule(
        source_id="vendor.daily",
        origin=Origin.CONFIG,
        schedule=CronSchedule("0 0 * * *", UTC_ZONE, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(),
        grace=timedelta(hours=1),
        target=FreshnessTarget(relation="raw.daily", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    result = evaluate(rule, RawObservation(start.replace(tzinfo=None)), now, provider())
    assert result.status is Status.OVERDUE
    assert result.release is not None
    assert result.release.instant == start + timedelta(days=1)
    assert result.missed_count == 1829
    assert result.pending_count == 1
    assert result.missed_truncated is False


def test_v7_release_at_the_local_midnight_of_active_from() -> None:
    """Kills mutant V7: the ``active_from`` floor is inclusive when the table is empty.

    A ``business_days`` release at exactly local midnight of ``active_from`` (here Monday
    2026-09-28 00:00 UTC) is the first obligation. The ``inclusive = False`` mutant skips
    it, finds no release at all within ``(start, now]`` and returns ON_TIME instead of
    OVERDUE; the boundary is the floor itself, which belongs to the schedule.
    """
    rule = SourceRule(
        source_id="vendor.midnight",
        origin=Origin.CONFIG,
        schedule=BusinessDaysSchedule(at=time(0, 0), timezone=UTC_ZONE),
        calendar=CalendarSpec(),
        grace=timedelta(hours=1),
        target=FreshnessTarget(relation="raw.midnight", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
        active_from=date(2026, 9, 28),
    )
    result = evaluate(rule, RawObservation(None), aware("2026-09-28T02:00:00Z"), provider())
    assert result.status is Status.OVERDUE
    assert result.release is not None
    assert result.release.instant == datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
    assert result.missed_count == 1
    assert result.pending_count == 0


def test_v9_step_two_is_bounded_by_the_start_horizon() -> None:
    """Kills mutant V9: step 2 stops at ``start + SEARCH_HORIZON``.

    2100 is not a leap year, so no release follows 2096-02-29 for 2922 days — longer than
    the horizon. The ``until=now`` mutant searches past the rule's own horizon, finds the
    pending 2104-02-29 release and answers NOT_DUE where the honest answer is E215
    ("cannot decide"); the boundary is what separates a verdict from a guess.
    """
    result = evaluate(
        leap_rule(),
        RawObservation(naive("2096-03-01 00:00:00")),
        aware("2104-03-01T00:00:00Z"),
        provider(),
    )
    assert result.status is Status.CONFIG_ERROR
    assert result.error is not None
    assert result.error.code == "E215"
    assert result.release is None
    assert result.next_expected_arrival is None


def test_v10_a_plain_cron_never_earns_w005() -> None:
    """V10: W005 requires the rule to consult its calendar at all (§3.3 as amended by A-11).

    A cron expression with `on_non_business_day: none` never asks the calendar anything, so
    it must not warn about `valid_until` even when the notice window reaches past it.
    """
    base = ecb_rule(schedule=CronSchedule("0 6 * * *", UTC_ZONE, NonBusinessDayPolicy.NONE))
    rule = SourceRule(
        source_id=base.source_id,
        origin=base.origin,
        schedule=base.schedule,
        calendar=CalendarSpec(valid_until=date(2026, 10, 1)),
        grace=base.grace,
        target=base.target,
        observed_timezone=base.observed_timezone,
        active_from=base.active_from,
    )
    # An aware observed value (so no W002 for the configured zone) and a `now` inside the
    # window: the only warning that could appear is the W005 this rule must not earn.
    result = evaluate(
        rule,
        RawObservation(datetime(2026, 9, 28, 5, 0, tzinfo=UTC)),
        datetime(2026, 9, 28, 6, 0, tzinfo=UTC),
        FakeCalendarProvider(),
    )
    assert "W005" not in [issue.code for issue in result.warnings]
