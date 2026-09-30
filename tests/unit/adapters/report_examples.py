"""The specified report examples (``tests/fixtures/spec/``), built from domain objects.

The reporters' tests share these builders so that the check example is defined once and
both renderers are compared against the same input.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from freshcal.core.errors import Issue
from freshcal.core.model import (
    CheckReport,
    EvaluationResult,
    NextEntry,
    NextRelease,
    NextReport,
    Observation,
    Origin,
    Release,
    Status,
)

BERLIN = ZoneInfo("Europe/Berlin")
EVALUATED_AT = datetime(2026, 9, 28, 5, 30, tzinfo=UTC)


def make_release(
    instant: str,
    local: str,
    *,
    adjusted_from: object = None,
    dst: str = "normal",
    clamped: bool = False,
) -> Release:
    return Release(
        instant=datetime.fromisoformat(instant),
        local=datetime.fromisoformat(local),
        adjusted_from=adjusted_from,  # type: ignore[arg-type]
        dst=dst,  # type: ignore[arg-type]
        clamped=clamped,
    )


def ecb_result() -> EvaluationResult:
    return EvaluationResult(
        source_id="ecb.fx_rates",
        origin=Origin.CONFIG,
        status=Status.ON_TIME,
        evaluated_at=EVALUATED_AT,
        schedule_timezone="Europe/Berlin",
        release=make_release("2026-09-25T14:00:00Z", "2026-09-25T16:00:00+02:00"),
        deadline=datetime(2026, 9, 25, 16, 0, tzinfo=UTC),
        observation=Observation(
            instant=datetime(2026, 9, 25, 14, 7, tzinfo=UTC),
            raw=datetime(2026, 9, 25, 14, 7),
            was_naive=True,
            interpreted_timezone="UTC",
        ),
        next_expected_arrival=datetime(2026, 9, 28, 14, 0, tzinfo=UTC),
        explanation=(
            "On time: latest release Fri 2026-09-25 16:00 CEST arrived (observed Fri "
            "2026-09-25 16:07 CEST); next release Mon 2026-09-28 16:00 CEST."
        ),
    )


def monthly_result() -> EvaluationResult:
    return EvaluationResult(
        source_id="stats.monthly_report",
        origin=Origin.DBT_MANIFEST,
        status=Status.ON_TIME,
        evaluated_at=EVALUATED_AT,
        schedule_timezone="Europe/Berlin",
        release=make_release("2026-09-03T07:00:00Z", "2026-09-03T09:00:00+02:00"),
        deadline=datetime(2026, 9, 3, 11, 0, tzinfo=UTC),
        observation=Observation(
            instant=datetime(2026, 9, 3, 7, 10, tzinfo=UTC),
            raw=datetime(2026, 9, 3, 7, 10, tzinfo=UTC),
            was_naive=False,
            interpreted_timezone=None,
        ),
        next_expected_arrival=datetime(2026, 10, 5, 7, 0, tzinfo=UTC),
        explanation=(
            "On time: latest release Thu 2026-09-03 09:00 CEST arrived (observed Thu "
            "2026-09-03 09:10 CEST); next release Mon 2026-10-05 09:00 CEST."
        ),
    )


def vendor_result() -> EvaluationResult:
    return EvaluationResult(
        source_id="vendor.daily_prices",
        origin=Origin.CONFIG,
        status=Status.OVERDUE,
        evaluated_at=EVALUATED_AT,
        schedule_timezone="UTC",
        release=make_release("2026-09-27T06:00:00Z", "2026-09-27T06:00:00+00:00"),
        deadline=datetime(2026, 9, 27, 7, 0, tzinfo=UTC),
        observation=Observation(
            instant=datetime(2026, 9, 26, 6, 3, tzinfo=UTC),
            raw=datetime(2026, 9, 26, 6, 3, tzinfo=UTC),
            was_naive=False,
            interpreted_timezone=None,
        ),
        next_expected_arrival=datetime(2026, 9, 28, 6, 0, tzinfo=UTC),
        missed_count=1,
        explanation=(
            "Overdue: release Sun 2026-09-27 06:00 UTC missed its deadline Sun 2026-09-27 "
            "07:00 UTC; 1 release missed; latest data observed Sat 2026-09-26 06:03 UTC."
        ),
        warnings=(
            Issue(
                "W002",
                "observed_timezone 'UTC' ignored because the value is timezone-aware",
                "vendor.daily_prices",
            ),
        ),
    )


def example_check_report() -> CheckReport:
    """The three sources of the §8.2 JSON example and the §8.3 table example."""
    return CheckReport(
        evaluated_at=EVALUATED_AT,
        results=(ecb_result(), monthly_result(), vendor_result()),
        exit_code=1,
    )


def example_next_report() -> NextReport:
    """The ECB source's first three upcoming releases (§6.2)."""
    releases = tuple(
        NextRelease(
            instant=datetime(2026, 9, day, 14, 0, tzinfo=UTC),
            local=datetime(2026, 9, day, 16, 0, tzinfo=BERLIN),
            deadline=datetime(2026, 9, day, 16, 0, tzinfo=UTC),
        )
        for day in (28, 29, 30)
    )
    return NextReport(
        evaluated_at=EVALUATED_AT,
        sources=(
            NextEntry(
                source_id="ecb.fx_rates",
                schedule_timezone="Europe/Berlin",
                releases=releases,
            ),
        ),
    )


def warning_issue() -> Issue:
    return Issue(
        "W005",
        "calendar target was consulted for 2027-01-04, after its valid_until 2026-12-31; "
        "the next expected arrival may be wrong",
        "ecb.fx_rates",
    )


def offset_local() -> datetime:
    return datetime(2026, 9, 25, 23, 7, tzinfo=timezone(timedelta(hours=9)))
