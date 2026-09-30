"""Golden scenarios: every §3.10 row is an executable test.

40 scenarios x 3 process time zones = 120 runs. Every run uses the real
``HolidaysCalendarProvider`` and must give identical results under all three zones
(§3.8.14).
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import pytest
from tests.golden.scenarios_loader import (
    PROCESS_TIME_ZONES,
    SCENARIO_IDS,
    SCENARIOS,
    instant,
    observation_of,
    rule_of,
)

from freshcal.adapters.holidays_provider import HolidaysCalendarProvider
from freshcal.core.calendar import BusinessCalendar
from freshcal.core.schedule import next_release_after
from freshcal.core.verdict import evaluate


@pytest.fixture(autouse=True)
def _restore_process_timezone() -> Iterator[None]:
    """Put the process time zone back after each run (TEST-08: the tests leaked it).

    Every case sets ``TZ`` and calls ``time.tzset()``; without this teardown the last case's
    zone stays active for the rest of the session, which makes any later test that reads the
    process zone order-dependent.
    """
    original = os.environ.get("TZ")
    yield
    if original is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = original
    time.tzset()


@pytest.mark.parametrize("process_timezone", PROCESS_TIME_ZONES)
@pytest.mark.parametrize("entry", SCENARIOS, ids=SCENARIO_IDS)
def test_golden_scenario(
    entry: dict[str, Any], process_timezone: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", process_timezone)
    time.tzset()

    scenario_id = str(entry["id"])
    expect = entry["expect"]
    assert isinstance(expect, dict)

    rule = rule_of(entry)
    now = datetime.fromisoformat(str(entry["now"]))
    result = evaluate(rule, observation_of(entry), now, HolidaysCalendarProvider())

    assert result.status.value == expect["status"], scenario_id

    release = result.release
    if expect["release"] is None:
        assert release is None, scenario_id
    else:
        assert release is not None, scenario_id
        assert release.instant == instant(expect["release"]), scenario_id
        if "release_adjusted_from" in expect:
            assert release.adjusted_from is not None
            assert release.adjusted_from.isoformat() == expect["release_adjusted_from"], scenario_id
        else:
            assert release.adjusted_from is None, scenario_id
        if "release_dst" in expect:
            assert release.dst == expect["release_dst"], scenario_id
        else:
            assert release.dst == "normal", scenario_id
        if "release_clamped" in expect:
            assert release.clamped is expect["release_clamped"], scenario_id
        else:
            assert release.clamped is False, scenario_id

    assert result.deadline == instant(expect["deadline"]), scenario_id
    assert result.next_expected_arrival == instant(expect["next_expected_arrival"]), scenario_id
    if "next_adjusted_from" in expect or "next_dst" in expect:
        provider = HolidaysCalendarProvider()
        upcoming = next_release_after(
            rule,
            now,
            BusinessCalendar(rule.calendar, provider, source_id=rule.source_id),
        )
        assert upcoming is not None, scenario_id
        assert upcoming.instant == result.next_expected_arrival, scenario_id
        if "next_adjusted_from" in expect:
            assert upcoming.adjusted_from is not None, scenario_id
            assert upcoming.adjusted_from.isoformat() == expect["next_adjusted_from"], scenario_id
        if "next_dst" in expect:
            assert upcoming.dst == expect["next_dst"], scenario_id

    assert result.missed_count == expect["missed_count"], scenario_id
    assert result.missed_truncated is expect["missed_truncated"], scenario_id
    assert result.pending_count == expect["pending_count"], scenario_id

    warning_codes = [issue.code for issue in result.warnings]
    assert warning_codes == list(expect["warning_codes"]), scenario_id
    assert (result.error.code if result.error is not None else None) == expect["error_code"], (
        scenario_id
    )
    assert result.explanation == expect["explanation"], scenario_id
