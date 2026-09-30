"""Tests for the explanation sentences and the explain trace (§8.4, §6.2)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from tests.conftest import FakeCalendarProvider

from freshcal.core.explain import explain_lines, explanation
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    CronSchedule,
    EvaluationResult,
    FreshnessTarget,
    HolidayCalendarRef,
    MonthlyBusinessDaySchedule,
    NonBusinessDayPolicy,
    Observation,
    Origin,
    RawObservation,
    Release,
    SourceRule,
    Status,
)
from freshcal.core.verdict import evaluate

BERLIN = ZoneInfo("Europe/Berlin")
UTC_ZONE = ZoneInfo("UTC")
XECB = HolidayCalendarRef("financial", "XECB")

ECB_HOLIDAYS = {
    ("financial XECB", 2026): {
        date(2026, 4, 3): "Good Friday",
        date(2026, 4, 6): "Easter Monday",
        date(2026, 12, 25): "Christmas Day",
        date(2026, 12, 26): "Christmas Holiday",
    }
}


def provider(**kwargs: object) -> FakeCalendarProvider:
    return FakeCalendarProvider(ECB_HOLIDAYS, **kwargs)  # type: ignore[arg-type]


def ecb_rule(
    *,
    grace: timedelta = timedelta(hours=2),
    active_from: date | None = None,
    schedule: object | None = None,
    calendar: CalendarSpec | None = None,
) -> SourceRule:
    return SourceRule(
        source_id="ecb.fx_rates",
        origin=Origin.CONFIG,
        schedule=schedule or BusinessDaysSchedule(at=time(16, 0), timezone=BERLIN),  # type: ignore[arg-type]
        calendar=calendar or CalendarSpec(holiday_calendars=(XECB,)),
        grace=grace,
        target=FreshnessTarget(relation="raw.ecb_fx_rates", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
        active_from=active_from,
    )


def aware(text: str) -> datetime:
    return datetime.fromisoformat(text)


def naive(text: str) -> datetime:
    return datetime.fromisoformat(text)


def evaluate_ecb(observed: object, now: str, **rule_kwargs: object) -> EvaluationResult:
    raw = RawObservation(observed)  # type: ignore[arg-type]
    return evaluate(ecb_rule(**rule_kwargs), raw, aware(now), provider())


def test_u_exp_01_on_time_with_release() -> None:
    result = evaluate_ecb(naive("2026-09-25 14:07:00"), "2026-09-28T05:30:00Z")
    assert result.status is Status.ON_TIME
    assert result.explanation == (
        "On time: latest release Fri 2026-09-25 16:00 CEST arrived (observed Fri "
        "2026-09-25 16:07 CEST); next release Mon 2026-09-28 16:00 CEST."
    )


def test_u_exp_01_on_time_without_any_release() -> None:
    """G23a: `active_from` in the future and an empty table: nothing has occurred yet."""
    result = evaluate_ecb(None, "2026-09-30T12:00:00Z", active_from=date(2026, 10, 1))
    assert result.status is Status.ON_TIME
    assert result.explanation == (
        "On time: no release has occurred yet; next release Thu 2026-10-01 16:00 CEST."
    )


def test_u_exp_02_not_due_singular_and_plural() -> None:
    single = evaluate_ecb(naive("2026-09-25 14:07:00"), "2026-09-28T14:30:00Z")
    assert single.status is Status.NOT_DUE
    assert single.explanation == (
        "Not due: release Mon 2026-09-28 16:00 CEST has not arrived yet; grace window "
        "ends Mon 2026-09-28 18:00 CEST; next release Tue 2026-09-29 16:00 CEST."
    )

    # An hourly UTC cron with 3h grace: two releases pending at 12:30Z.
    hourly = ecb_rule(
        grace=timedelta(hours=3),
        schedule=CronSchedule("0 * * * *", UTC_ZONE, NonBusinessDayPolicy.NONE),
    )
    result = evaluate(
        hourly,
        RawObservation(naive("2026-09-28 10:05:00")),
        aware("2026-09-28T12:30:00Z"),
        provider(),
    )
    assert result.status is Status.NOT_DUE
    assert result.pending_count == 2
    assert result.explanation == (
        "Not due: release Mon 2026-09-28 11:00 UTC has not arrived yet (2 releases "
        "pending); grace window ends Mon 2026-09-28 14:00 UTC; next release Mon "
        "2026-09-28 13:00 UTC."
    )


def test_u_exp_03_overdue_singular_plural_and_never() -> None:
    single = evaluate_ecb(naive("2026-09-25 14:07:00"), "2026-09-28T16:00:01Z")
    assert single.status is Status.OVERDUE
    assert single.explanation == (
        "Overdue: release Mon 2026-09-28 16:00 CEST missed its deadline Mon 2026-09-28 "
        "18:00 CEST; 1 release missed; latest data observed Fri 2026-09-25 16:07 CEST."
    )

    # Three consecutive misses (G32), and NULL data rendered as "never".
    three = evaluate_ecb(naive("2026-09-25 14:07:00"), "2026-10-01T10:00:00Z")
    assert three.missed_count == 3
    assert three.explanation == (
        "Overdue: release Mon 2026-09-28 16:00 CEST missed its deadline Mon 2026-09-28 "
        "18:00 CEST; 3 releases missed; latest data observed Fri 2026-09-25 16:07 CEST."
    )

    never = evaluate_ecb(None, "2026-10-01T16:30:00Z", active_from=date(2026, 10, 1))
    assert never.status is Status.OVERDUE
    assert never.explanation.endswith("latest data observed never.")


def test_u_exp_03_overdue_truncated_uses_at_least() -> None:
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
    result = evaluate(
        rule, RawObservation((now - timedelta(days=10)).replace(tzinfo=None)), now, provider()
    )
    assert result.missed_truncated is True
    assert "at least 10000 releases missed" in result.explanation


def test_u_exp_04_no_data() -> None:
    result = evaluate_ecb(None, "2026-09-28T05:30:00Z")
    assert result.status is Status.NO_DATA
    assert result.explanation == (
        "No data: max(_loaded_at) returned NULL; latest release Fri 2026-09-25 16:00 "
        "CEST; next release Mon 2026-09-28 16:00 CEST."
    )

    # With no release at all in the horizon the sentence says "none" and the next part
    # says there is no next release within 1830 days.
    empty = EvaluationResult(
        source_id="x",
        origin=Origin.CONFIG,
        status=Status.NO_DATA,
        evaluated_at=aware("2026-09-28T05:30:00Z"),
        schedule_timezone="Europe/Berlin",
        release=None,
        deadline=None,
        observation=None,
        next_expected_arrival=None,
    )
    assert explanation(empty, loaded_at_field="_loaded_at") == (
        "No data: max(_loaded_at) returned NULL; latest release none; no next release "
        "within 1830 days."
    )


def test_u_exp_05_config_error() -> None:
    result = evaluate_ecb(
        None,
        "2027-01-04T02:00:00Z",
        calendar=CalendarSpec(name="cn_workdays", valid_until=date(2026, 12, 31)),
    )
    assert result.status is Status.CONFIG_ERROR
    assert result.error is not None
    assert result.explanation == f"Configuration error: {result.error.code} {result.error.message}"
    assert result.explanation.startswith("Configuration error: E408 ")


def test_u_exp_06_query_error() -> None:
    from freshcal.core.errors import Issue

    issue = Issue("E502", "query failed: connection refused", "vendor.daily")
    result = EvaluationResult(
        source_id="vendor.daily",
        origin=Origin.CONFIG,
        status=Status.QUERY_ERROR,
        evaluated_at=aware("2026-09-28T05:30:00Z"),
        schedule_timezone="UTC",
        release=None,
        deadline=None,
        observation=None,
        next_expected_arrival=None,
        error=issue,
    )
    assert explanation(result) == "Query error: E502 query failed: connection refused"


def test_u_exp_07_trace_for_g02_contains_releases_weekend_lines_and_verdict() -> None:
    lines = explain_lines(
        ecb_rule(),
        RawObservation(naive("2026-09-25 14:07:00")),
        aware("2026-09-28T14:30:00Z"),
        provider(),
        origin_label="config examples/ecb/freshcal.yml, sources[0]",
        query_text="SELECT max(_loaded_at) AS observed FROM raw.ecb_fx_rates",
    )
    text = "\n".join(lines)
    assert "Source    ecb.fx_rates (config examples/ecb/freshcal.yml, sources[0])" in text
    assert "Schedule  business_days at 16:00 Europe/Berlin" in text
    assert "Calendar  weekend: sat, sun | holidays: financial XECB | overrides: none" in text
    assert "Grace     2h (wall-clock)" in text
    assert "Query     SELECT max(_loaded_at) AS observed FROM raw.ecb_fx_rates" in text
    assert "Now       2026-09-28T14:30:00Z = Mon 2026-09-28 16:30 CEST" in text
    assert "Observed  2026-09-25T14:07:00Z = Fri 2026-09-25 16:07 CEST" in text
    assert "Releases around now (Europe/Berlin)" in text
    assert "  Fri 2026-09-25 16:00 CEST  2026-09-25T14:00:00Z  arrived" in text
    assert "  Sat 2026-09-26  no release: weekend" in text
    assert "  Sun 2026-09-27  no release: weekend" in text
    assert "  Mon 2026-09-28 16:00 CEST  2026-09-28T14:00:00Z  occurred, not arrived" in text
    assert "  1. First release after the observed timestamp: Mon 2026-09-28 16:00 CEST." in text
    assert "  => NOT_DUE" in text
    assert lines[-1] == (
        "Result    NOT_DUE: Not due: release Mon 2026-09-28 16:00 CEST has not arrived yet; "
        "grace window ends Mon 2026-09-28 18:00 CEST; next release Tue 2026-09-29 16:00 CEST."
    )


def test_u_exp_07_trace_lists_holiday_reason_for_a_holiday_gap() -> None:
    lines = explain_lines(
        ecb_rule(),
        RawObservation(naive("2026-04-02 14:05:00")),
        aware("2026-04-07T06:00:00Z"),
        provider(),
    )
    text = "\n".join(lines)
    assert "  Fri 2026-04-03  no release: holiday: Good Friday (financial XECB)" in text
    assert "  Mon 2026-04-06  no release: holiday: Easter Monday (financial XECB)" in text


def test_u_exp_08_annotations_for_rolled_dst_and_clamped_releases() -> None:
    # A rolled release: `0 9 15 * *` in Berlin with `preceding` moves 2026-11-15 (Sunday)
    # to Friday 2026-11-13.
    rolled = SourceRule(
        source_id="vendor.monthly",
        origin=Origin.CONFIG,
        schedule=CronSchedule("0 9 15 * *", BERLIN, NonBusinessDayPolicy.PRECEDING),
        calendar=CalendarSpec(),
        grace=timedelta(hours=8),
        target=FreshnessTarget(relation="raw.monthly", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    text = "\n".join(
        explain_lines(
            rolled,
            RawObservation(naive("2026-10-15 07:30:00")),
            aware("2026-11-13T12:00:00Z"),
            provider(),
        )
    )
    assert "adjusted from 2026-11-15" in text

    # A DST gap: 02:30 does not exist in Berlin on 2026-03-29.
    gap = SourceRule(
        source_id="vendor.nightly",
        origin=Origin.CONFIG,
        schedule=CronSchedule("30 2 * * *", BERLIN, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(),
        grace=timedelta(hours=1),
        target=FreshnessTarget(relation="raw.nightly", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    text = "\n".join(
        explain_lines(
            gap,
            RawObservation(naive("2026-03-28 01:35:00")),
            aware("2026-03-29T02:00:00Z"),
            provider(),
        )
    )
    assert "DST gap: shifted to 03:30" in text

    # A DST overlap: 02:30 occurs twice on 2026-10-25 and the first occurrence is used.
    text = "\n".join(
        explain_lines(
            gap,
            RawObservation(naive("2026-10-24 00:40:00")),
            aware("2026-10-25T01:45:00Z"),
            provider(),
        )
    )
    assert "DST overlap: first occurrence" in text

    # A clamped monthly release: the 22nd business day of February 2026 does not exist.
    clamped = SourceRule(
        source_id="vendor.monthly",
        origin=Origin.CONFIG,
        schedule=MonthlyBusinessDaySchedule(business_day=22, at=time(18, 0), timezone=UTC_ZONE),
        calendar=CalendarSpec(),
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation="raw.monthly", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    text = "\n".join(
        explain_lines(
            clamped,
            RawObservation(naive("2026-01-30 18:05:00")),
            aware("2026-02-27T19:00:00Z"),
            provider(),
        )
    )
    assert "clamped" in text


def test_u_exp_09_evaluate_never_returns_an_empty_explanation() -> None:
    """Every U-VER fixture produces a non-empty sentence."""
    scenarios: list[tuple[SourceRule, RawObservation, datetime]] = [
        (ecb_rule(), RawObservation(None), aware("2026-09-28T05:30:00Z")),
        (ecb_rule(), RawObservation(naive("2026-09-25 14:07:00")), aware("2026-09-28T05:30:00Z")),
        (ecb_rule(), RawObservation(naive("2026-09-25 14:07:00")), aware("2026-09-28T14:30:00Z")),
        (ecb_rule(), RawObservation(naive("2026-09-25 14:07:00")), aware("2026-09-28T16:00:01Z")),
        (
            ecb_rule(schedule=CronSchedule("0 0 30 2 *", UTC_ZONE, NonBusinessDayPolicy.NONE)),
            RawObservation(naive("2026-09-25 14:07:00")),
            aware("2026-09-28T05:30:00Z"),
        ),
        (ecb_rule(), RawObservation(None), aware("2026-09-30T12:00:00Z")),
    ]
    for rule, raw, now in scenarios:
        result = evaluate(rule, raw, now, provider())
        assert result.explanation, (rule.source_id, result.status)
        assert not result.explanation.startswith("None")
        assert " " in result.explanation


def test_trace_never_raises_for_a_config_error() -> None:
    rule = ecb_rule(schedule=CronSchedule("0 0 30 2 *", UTC_ZONE, NonBusinessDayPolicy.NONE))
    lines = explain_lines(rule, RawObservation(None), aware("2026-09-28T05:30:00Z"), provider())
    text = "\n".join(lines)
    assert "cannot list releases: E209" in text
    assert "=> CONFIG_ERROR" in text


def test_trace_handles_a_null_observation() -> None:
    lines = explain_lines(
        ecb_rule(), RawObservation(None), aware("2026-09-28T05:30:00Z"), provider()
    )
    text = "\n".join(lines)
    assert "Observed  NULL" in text
    assert "=> NO_DATA" in text


def test_observation_dataclass_is_used_for_aware_values() -> None:
    """A trace of an aware value says so instead of mentioning observed_timezone."""
    lines = explain_lines(
        ecb_rule(),
        RawObservation(aware("2026-09-25T14:07:00Z")),
        aware("2026-09-28T05:30:00Z"),
        provider(),
    )
    assert "(timezone-aware value)" in "\n".join(lines)
    assert Observation.__name__ == "Observation"
    assert Release.__name__ == "Release"


def test_u_exp_10_on_time_reasoning_names_the_arrived_release() -> None:
    """CLI-06: a release at or before the observation is never called "after" it.

    ON_TIME is decided by the latest release at or before the observed timestamp (which
    arrived) and by the first release after it lying beyond ``now``.
    """
    text = "\n".join(
        explain_lines(
            ecb_rule(),
            RawObservation(naive("2026-09-25 14:07:00")),
            aware("2026-09-28T05:30:00Z"),
            provider(),
        )
    )
    assert "First release after the observed timestamp: Fri 2026-09-25" not in text
    assert (
        "  1. The latest release at or before the observed timestamp: "
        "Fri 2026-09-25 16:00 CEST arrived (observed Fri 2026-09-25 16:07 CEST)."
    ) in text
    assert (
        "  2. The first release after the observed timestamp, "
        "Mon 2026-09-28 16:00 CEST, is later than now (release > now)."
    ) in text
    assert "  3. Nothing due is outstanding" in text
    assert "  => ON_TIME" in text


def test_u_exp_11_only_the_first_future_release_is_the_next_expected_arrival() -> None:
    """CLI-14: the tag belongs to the next expected arrival alone."""
    text = "\n".join(
        explain_lines(
            ecb_rule(),
            RawObservation(naive("2026-09-25 14:07:00")),
            aware("2026-09-28T05:30:00Z"),
            provider(),
        )
    )
    assert text.count("(next expected arrival)") == 1
    assert (
        "  Mon 2026-09-28 16:00 CEST  2026-09-28T14:00:00Z  future (next expected arrival)" in text
    )
    assert "  Tue 2026-09-29 16:00 CEST  2026-09-29T14:00:00Z  future\n" in text


def test_u_exp_12_clamped_release_names_the_month_and_its_business_days() -> None:
    """CLI-14: not a bare "(clamped)" but the month and how many business days it had."""
    clamped = SourceRule(
        source_id="vendor.monthly",
        origin=Origin.CONFIG,
        schedule=MonthlyBusinessDaySchedule(business_day=22, at=time(18, 0), timezone=UTC_ZONE),
        calendar=CalendarSpec(),
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation="raw.monthly", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    text = "\n".join(
        explain_lines(
            clamped,
            RawObservation(naive("2026-01-30 18:05:00")),
            aware("2026-02-27T19:00:00Z"),
            provider(),
        )
    )
    assert "(clamped)" not in text
    assert "clamped: February 2026 has only 20 business days" in text


def test_u_exp_13_deadline_state_names_the_deadline() -> None:
    """CLI-14: "deadline not passed" says which deadline, in the schedule time zone."""
    not_due = "\n".join(
        explain_lines(
            ecb_rule(),
            RawObservation(naive("2026-09-25 14:07:00")),
            aware("2026-09-28T14:30:00Z"),
            provider(),
        )
    )
    assert "occurred, not arrived; deadline Mon 2026-09-28 18:00 CEST not passed" in not_due

    overdue = "\n".join(
        explain_lines(
            ecb_rule(),
            RawObservation(naive("2026-09-25 14:07:00")),
            aware("2026-09-28T16:00:01Z"),
            provider(),
        )
    )
    assert "occurred, not arrived; deadline Mon 2026-09-28 18:00 CEST passed" in overdue


def test_u_exp_14_a_single_truncated_miss_is_singular() -> None:
    """SEM-10: one truncated miss reads `at least 1 release missed`, never `1 releases`."""
    rule = SourceRule(
        source_id="vendor.leap",
        origin=Origin.CONFIG,
        schedule=CronSchedule("0 0 29 2 *", UTC_ZONE, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(),
        grace=timedelta(days=1),
        target=FreshnessTarget(relation="raw.leap", loaded_at_field="_loaded_at"),
    )
    result = evaluate(
        rule,
        RawObservation(aware("2020-01-01T00:00:00Z")),
        aware("2030-01-01T00:00:00Z"),
        provider(),
    )
    assert result.status is Status.OVERDUE
    assert result.missed_count == 1
    assert result.missed_truncated is True
    assert "at least 1 release missed" in result.explanation
    assert "1 releases" not in result.explanation


G01_TRACE = (
    "Source    ecb.fx_rates (config examples/ecb/freshcal.yml, sources[0])\n"
    "Schedule  business_days at 16:00 Europe/Berlin\n"
    "Calendar  weekend: sat, sun | holidays: financial XECB | overrides: none\n"
    "Grace     2h (wall-clock)\n"
    "Query     SELECT max(_loaded_at) AS observed FROM raw.ecb_fx_rates\n"
    "Now       2026-09-28T05:30:00Z = Mon 2026-09-28 07:30 CEST\n"
    "Observed  2026-09-25T14:07:00Z = Fri 2026-09-25 16:07 CEST "
    "(timezone-aware value)\n"
    "\n"
    "Releases around now (Europe/Berlin)\n"
    "  Wed 2026-09-23 16:00 CEST  2026-09-23T14:00:00Z  arrived\n"
    "  Thu 2026-09-24 16:00 CEST  2026-09-24T14:00:00Z  arrived\n"
    "  Fri 2026-09-25 16:00 CEST  2026-09-25T14:00:00Z  arrived\n"
    "  Sat 2026-09-26  no release: weekend\n"
    "  Sun 2026-09-27  no release: weekend\n"
    "  Mon 2026-09-28 16:00 CEST  2026-09-28T14:00:00Z  future (next expected arrival)\n"
    "  Tue 2026-09-29 16:00 CEST  2026-09-29T14:00:00Z  future\n"
    "\n"
    "Reasoning\n"
    "  1. The latest release at or before the observed timestamp: Fri 2026-09-25 "
    "16:00 CEST arrived (observed Fri 2026-09-25 16:07 CEST).\n"
    "  2. The first release after the observed timestamp, Mon 2026-09-28 16:00 CEST, "
    "is later than now (release > now).\n"
    "  3. Nothing due is outstanding: no release in the searched interval is missing.\n"
    "  => ON_TIME\n"
    "\n"
    "Result    ON_TIME: On time: latest release Fri 2026-09-25 16:00 CEST arrived "
    "(observed Fri 2026-09-25 16:07 CEST); next release Mon 2026-09-28 16:00 CEST.\n"
)

CLAMPED_TRACE = (
    "Source    vendor.monthly\n"
    "Schedule  monthly_business_day 22 at 18:00 UTC\n"
    "Calendar  weekend: sat, sun | holidays: none | overrides: none\n"
    "Grace     2h (wall-clock)\n"
    "Now       2026-02-27T19:00:00Z = Fri 2026-02-27 19:00 UTC\n"
    "Observed  2026-01-30T18:05:00Z = Fri 2026-01-30 18:05 UTC (naive value "
    "interpreted in UTC via observed_timezone)\n"
    "\n"
    "Releases around now (UTC)\n"
    "  Tue 2025-12-30 18:00 UTC  2025-12-30T18:00:00Z  arrived\n"
    "  Fri 2026-01-30 18:00 UTC  2026-01-30T18:00:00Z  arrived\n"
    "  Fri 2026-02-27 18:00 UTC  2026-02-27T18:00:00Z  occurred, not arrived; "
    "deadline Fri 2026-02-27 20:00 UTC not passed (clamped: February 2026 has only "
    "20 business days)\n"
    "  Tue 2026-03-31 18:00 UTC  2026-03-31T18:00:00Z  future (next expected arrival)\n"
    "  Thu 2026-04-30 18:00 UTC  2026-04-30T18:00:00Z  future\n"
    "\n"
    "Reasoning\n"
    "  1. First release after the observed timestamp: Fri 2026-02-27 18:00 UTC.\n"
    "  2. It has occurred (release <= now).\n"
    "  3. Its deadline Fri 2026-02-27 20:00 UTC has not passed (now <= deadline).\n"
    "  => NOT_DUE\n"
    "\n"
    "Result    NOT_DUE: Not due: release Fri 2026-02-27 18:00 UTC has not arrived "
    "yet; grace window ends Fri 2026-02-27 20:00 UTC; next release Tue 2026-03-31 "
    "18:00 UTC.\n"
)

HOLIDAY_GAP_TRACE = (
    "Source    ecb.fx_rates\n"
    "Schedule  business_days at 16:00 Europe/Berlin\n"
    "Calendar  weekend: sat, sun | holidays: financial XECB | overrides: none\n"
    "Grace     2h (wall-clock)\n"
    "Now       2026-04-07T06:00:00Z = Tue 2026-04-07 08:00 CEST\n"
    "Observed  2026-04-02T14:05:00Z = Thu 2026-04-02 16:05 CEST (naive value "
    "interpreted in UTC via observed_timezone)\n"
    "\n"
    "Releases around now (Europe/Berlin)\n"
    "  Tue 2026-03-31 16:00 CEST  2026-03-31T14:00:00Z  arrived\n"
    "  Wed 2026-04-01 16:00 CEST  2026-04-01T14:00:00Z  arrived\n"
    "  Thu 2026-04-02 16:00 CEST  2026-04-02T14:00:00Z  arrived\n"
    "  Fri 2026-04-03  no release: holiday: Good Friday (financial XECB)\n"
    "  Sat 2026-04-04  no release: weekend\n"
    "  Sun 2026-04-05  no release: weekend\n"
    "  Mon 2026-04-06  no release: holiday: Easter Monday (financial XECB)\n"
    "  Tue 2026-04-07 16:00 CEST  2026-04-07T14:00:00Z  future (next expected arrival)\n"
    "  Wed 2026-04-08 16:00 CEST  2026-04-08T14:00:00Z  future\n"
    "\n"
    "Reasoning\n"
    "  1. The latest release at or before the observed timestamp: Thu 2026-04-02 "
    "16:00 CEST arrived (observed Thu 2026-04-02 16:05 CEST).\n"
    "  2. The first release after the observed timestamp, Tue 2026-04-07 16:00 CEST, "
    "is later than now (release > now).\n"
    "  3. Nothing due is outstanding: no release in the searched interval is missing.\n"
    "  => ON_TIME\n"
    "\n"
    "Result    ON_TIME: On time: latest release Thu 2026-04-02 16:00 CEST arrived "
    "(observed Thu 2026-04-02 16:05 CEST); next release Tue 2026-04-07 16:00 CEST.\n"
)


def test_trace_snapshot_g01_on_time() -> None:
    """The whole G01 trace, exactly: the §8.4 sentence plus the §6.2 lines it comes from."""
    from freshcal.adapters.holidays_provider import HolidaysCalendarProvider

    lines = explain_lines(
        ecb_rule(),
        RawObservation(aware("2026-09-25T14:07:00Z")),
        aware("2026-09-28T05:30:00Z"),
        HolidaysCalendarProvider(),
        origin_label="config examples/ecb/freshcal.yml, sources[0]",
        query_text="SELECT max(_loaded_at) AS observed FROM raw.ecb_fx_rates",
    )
    assert "\n".join(lines) + "\n" == G01_TRACE


def test_trace_snapshot_clamped_release() -> None:
    """A clamped monthly release: the annotated line carries the month and its count."""
    clamped = SourceRule(
        source_id="vendor.monthly",
        origin=Origin.CONFIG,
        schedule=MonthlyBusinessDaySchedule(business_day=22, at=time(18, 0), timezone=UTC_ZONE),
        calendar=CalendarSpec(),
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation="raw.monthly", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    lines = explain_lines(
        clamped,
        RawObservation(naive("2026-01-30 18:05:00")),
        aware("2026-02-27T19:00:00Z"),
        provider(),
    )
    assert "\n".join(lines) + "\n" == CLAMPED_TRACE


def test_trace_snapshot_holiday_gap() -> None:
    """A holiday gap: every skipped local date with its reason, and the ON_TIME reasoning."""
    lines = explain_lines(
        ecb_rule(),
        RawObservation(naive("2026-04-02 14:05:00")),
        aware("2026-04-07T06:00:00Z"),
        provider(),
    )
    assert "\n".join(lines) + "\n" == HOLIDAY_GAP_TRACE
