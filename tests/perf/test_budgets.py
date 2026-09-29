"""Performance and resource budgets (BLUEPRINT.md §9.9).

Wall time is measured with ``time.perf_counter()``, which is allowed: only *reading the
wall clock for decisions* is banned (the clock rule of ADR 0010). The budgets are
roughly ten times the expected cost, so hardware noise does not fail them; if one fails
in CI, investigate first and raise it only through a Blueprint Amendment, never by
skipping the test.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from datetime import time as time_of_day
from pathlib import Path
from zoneinfo import ZoneInfo

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
)
from freshcal.core.verdict import evaluate

BERLIN = ZoneInfo("Europe/Berlin")
UTC_ZONE = ZoneInfo("UTC")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

XECB = HolidayCalendarRef("financial", "XECB")
ECB_HOLIDAYS = {
    ("financial XECB", 2026): {
        datetime(2026, 1, 1).date(): "New Year's Day",
        datetime(2026, 4, 3).date(): "Good Friday",
        datetime(2026, 12, 25).date(): "Christmas Day",
    }
}
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def ecb_rule(index: int) -> SourceRule:
    return SourceRule(
        source_id=f"perf.source_{index}",
        origin=Origin.CONFIG,
        schedule=BusinessDaysSchedule(at=time_of_day(16, 0), timezone=BERLIN),
        calendar=CalendarSpec(holiday_calendars=(XECB,)),
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )


def minutes_rule() -> SourceRule:
    return SourceRule(
        source_id="perf.minutely",
        origin=Origin.CONFIG,
        schedule=CronSchedule("* * * * *", UTC_ZONE, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(),
        grace=timedelta(hours=1),
        target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )


def daily_rule() -> SourceRule:
    return SourceRule(
        source_id="perf.daily",
        origin=Origin.CONFIG,
        schedule=CronSchedule("0 6 * * *", UTC_ZONE, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(),
        grace=timedelta(hours=1),
        target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )


def test_pb_1_evaluate_500_ecb_like_rules_with_fresh_data() -> None:
    provider = FakeCalendarProvider(ECB_HOLIDAYS)
    observed = RawObservation((NOW - timedelta(hours=1)).replace(tzinfo=None))
    rules = [ecb_rule(index) for index in range(500)]

    started = time.perf_counter()
    results = [evaluate(rule, observed, NOW, provider) for rule in rules]
    elapsed = time.perf_counter() - started

    assert len(results) == 500
    assert all(result.status.is_verdict for result in results)
    assert elapsed < 5.0, f"PB-1 took {elapsed:.2f}s (budget 5s)"


def test_pb_2_per_minute_rule_with_thirty_day_old_data() -> None:
    provider = FakeCalendarProvider()
    observed = RawObservation((NOW - timedelta(days=30)).replace(tzinfo=None))

    started = time.perf_counter()
    result = evaluate(minutes_rule(), observed, NOW, provider)
    elapsed = time.perf_counter() - started

    assert result.missed_count == 10_000
    assert result.missed_truncated is True
    # Budget raised from 2 s to 12 s by Blueprint Amendment A-5: the specified chunked
    # search materialises a 32-day window per search (~46 000 croniter calls for a
    # per-minute schedule). Measured 2.3 s plain and 5.5-6.1 s under `pytest --cov`, which
    # is how CI runs it.
    assert elapsed < 12.0, f"PB-2 took {elapsed:.2f}s (budget 12s, Amendment A-5)"


def test_pb_3_daily_rule_with_a_sentinel_observed_timestamp() -> None:
    provider = FakeCalendarProvider(start_year=1999, end_year=2100)
    observed = RawObservation(datetime(1970, 1, 1))

    started = time.perf_counter()
    result = evaluate(daily_rule(), observed, NOW, provider)
    elapsed = time.perf_counter() - started

    assert result.status.value == "OVERDUE"
    assert result.missed_truncated is True
    assert elapsed < 1.0, f"PB-3 took {elapsed:.2f}s (budget 1s)"


def test_pb_5_property_suite_under_the_ci_profile() -> None:
    """The whole property directory, as CI runs it, stays inside the PB-5 budget."""
    environment = dict(os.environ)
    environment["HYPOTHESIS_PROFILE"] = "ci"
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/property", "-q", "-p", "no:cacheprovider"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.perf_counter() - started

    assert completed.returncode == 0, completed.stdout[-2000:]
    assert elapsed < 180.0, f"PB-5 took {elapsed:.1f}s (budget 180s)"
