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

from freshcal.app import run_next
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    CronSchedule,
    FreshnessTarget,
    HolidayCalendarRef,
    NonBusinessDayPolicy,
    Origin,
    RawObservation,
    SourceEntry,
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


def _next_entry(index: int) -> SourceEntry:
    rule = SourceRule(
        source_id=f"perf.next_{index}",
        origin=Origin.CONFIG,
        schedule=CronSchedule("* * * * *", UTC_ZONE, NonBusinessDayPolicy.NONE),
        calendar=CalendarSpec(),
        grace=timedelta(hours=1),
        target=FreshnessTarget(relation="raw.t", loaded_at_field="_loaded_at"),
        observed_timezone=UTC_ZONE,
    )
    return SourceEntry(source_id=rule.source_id, origin=Origin.CONFIG, rule=rule)


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
    # The blueprint's 2 s budget holds because the search streams releases and stops at
    # the counting cap instead of materialising a 32-day window (Amendment A-5 records the
    # investigation: 2.29 s before the fix, ~0.3 s after).
    assert elapsed < 2.0, f"PB-2 took {elapsed:.2f}s (budget 2s)"


def test_pb_3_daily_rule_with_a_sentinel_observed_timestamp() -> None:
    provider = FakeCalendarProvider(start_year=1999, end_year=2100)
    observed = RawObservation(datetime(1970, 1, 1))

    started = time.perf_counter()
    result = evaluate(daily_rule(), observed, NOW, provider)
    elapsed = time.perf_counter() - started

    assert result.status.value == "OVERDUE"
    assert result.missed_truncated is True
    assert elapsed < 1.0, f"PB-3 took {elapsed:.2f}s (budget 1s)"


def test_pb_4_run_next_with_count_100_for_50_minute_rules() -> None:
    """PB-4: 50 per-minute sources, 100 upcoming releases each."""
    provider = FakeCalendarProvider()
    entries = [_next_entry(index) for index in range(50)]

    started = time.perf_counter()
    report = run_next(entries, provider, NOW, count=100)
    elapsed = time.perf_counter() - started

    assert len(report.sources) == 50
    assert all(len(entry.releases) == 100 for entry in report.sources)
    assert elapsed < 2.0, f"PB-4 took {elapsed:.2f}s (budget 2s)"


def test_pb_5_property_suite_under_the_ci_profile() -> None:
    """The whole property directory, as CI runs it, stays inside the PB-5 budget."""
    environment = dict(os.environ)
    environment["HYPOTHESIS_PROFILE"] = "ci"
    # Import the package from the source tree as well: the subprocess must not depend on
    # the venv's editable-install .pth being readable.
    source_path = str(REPOSITORY_ROOT / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        [source_path, environment.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
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


#: The schedule mix of `review/v0.1.0/E-blackbox/perf/small200.yml`, in file order.
MIXED_SCHEDULES = (
    'schedule: {kind: business_days, time: "16:00"}',
    'schedule: {kind: cron, cron: "*/15 * * * *"}',
    'schedule: {kind: monthly_business_day, business_day: -1, time: "17:00"}',
    'schedule: {kind: cron, cron: "0 9-17 * * 1-5", on_non_business_day: following}',
)


def mixed_config(count: int) -> str:
    """A config with ``count`` sources cycling through the auditor's schedule mix."""
    lines = [
        "version: 1",
        "defaults: {timezone: Europe/Berlin, grace: 2h, calendar: target}",
        "calendars:",
        "  target: {holidays: [{financial: XECB}]}",
        "sources:",
    ]
    for index in range(count):
        lines.append(f"  - name: perf.mix{index:04d}")
        lines.append("    relation: raw.small")
        lines.append("    loaded_at_field: loaded_at")
        lines.append(f"    {MIXED_SCHEDULES[index % len(MIXED_SCHEDULES)]}")
    return "\n".join(lines) + "\n"


def test_pb_6_run_validate_for_500_mixed_sources() -> None:
    """PB-6: `validate` for 500 sources of mixed kinds stays under ten seconds.

    Built from the schedule mix of `review/v0.1.0/E-blackbox/perf/small200.yml` (finding
    E2E-04 measured `validate` at 36 s for 5 000 such sources). `validate` opens no
    connection, so no database is involved; the run uses the real loader and the fake
    calendar provider, which is what the budget is about.
    """
    from freshcal.app import run_validate
    from freshcal.config.loader import load_config

    config = load_config(_write_temp_config(mixed_config(500)))
    provider = FakeCalendarProvider(ECB_HOLIDAYS)
    entries = list(config.entries)

    started = time.perf_counter()
    report = run_validate(entries, provider, NOW)
    elapsed = time.perf_counter() - started

    assert len(entries) == 500
    assert report.valid_count == 500, [issue.message for issue in report.errors][:3]
    assert elapsed < 10.0, f"PB-6 took {elapsed:.2f}s (budget 10s)"


def _write_temp_config(text: str) -> Path:
    import tempfile

    directory = Path(tempfile.mkdtemp(prefix="freshcal_pb6_"))
    path = directory / "freshcal.yml"
    path.write_text(text)
    (directory / "target").mkdir(exist_ok=True)
    return path
