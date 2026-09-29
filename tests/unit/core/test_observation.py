"""Tests for observed-timestamp normalization (BLUEPRINT.md §3.7.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from freshcal.core.errors import ConfigError
from freshcal.core.model import RawObservation
from freshcal.core.observation import FUTURE_SKEW_TOLERANCE, normalize_observed

BERLIN = ZoneInfo("Europe/Berlin")
UTC_ZONE = ZoneInfo("UTC")
NOW = datetime(2026, 9, 28, 16, 30, tzinfo=UTC)


def test_u_obs_01_null_value() -> None:
    observation, warnings = normalize_observed(
        RawObservation(None), None, NOW, loaded_at_field="_loaded_at"
    )
    assert observation is None
    assert warnings == []
    # A configured zone makes no difference for NULL.
    assert normalize_observed(RawObservation(None), BERLIN, NOW) == (None, [])


def test_u_obs_02_naive_without_zone_is_e214() -> None:
    raw = RawObservation(datetime(2026, 9, 28, 15, 30))
    with pytest.raises(ConfigError) as excinfo:
        normalize_observed(raw, None, NOW, loaded_at_field="_loaded_at")
    issue = excinfo.value.issue
    assert issue.code == "E214"
    assert issue.message == (
        "max(_loaded_at) returned a timestamp without time zone (2026-09-28 15:30:00); "
        "set observed_timezone on the source or under defaults (write UTC explicitly if "
        "the column stores UTC)"
    )
    # The default field name is used when the caller does not pass one.
    with pytest.raises(ConfigError) as excinfo:
        normalize_observed(raw, None, NOW)
    assert excinfo.value.issue.message.startswith("max(loaded_at_field) returned")


def test_u_obs_03_naive_values_are_interpreted_in_the_configured_zone() -> None:
    # G20a: a naive Berlin-local value at 15:30 is 13:30Z, before the 14:00Z release.
    observation, warnings = normalize_observed(
        RawObservation(datetime(2026, 9, 28, 15, 30)), BERLIN, NOW
    )
    assert observation is not None
    assert observation.instant == datetime(2026, 9, 28, 13, 30, tzinfo=UTC)
    assert observation.was_naive is True
    assert observation.interpreted_timezone == "Europe/Berlin"
    assert observation.raw == datetime(2026, 9, 28, 15, 30)
    assert warnings == []

    # Explicit UTC counts as configured (U-LOAD-11's rule, at normalization level).
    utc_observation, _ = normalize_observed(
        RawObservation(datetime(2026, 9, 28, 15, 30)), UTC_ZONE, NOW
    )
    assert utc_observation is not None
    assert utc_observation.instant == datetime(2026, 9, 28, 15, 30, tzinfo=UTC)
    assert utc_observation.interpreted_timezone == "UTC"


def test_u_obs_03_naive_values_in_a_dst_overlap_use_fold_zero() -> None:
    # 2026-10-25 02:30 Berlin occurs twice; fold=0 keeps the first (CEST, 00:30Z).
    observation, _ = normalize_observed(
        RawObservation(datetime(2026, 10, 25, 2, 30)),
        BERLIN,
        datetime(2026, 10, 25, 3, 0, tzinfo=UTC),
    )
    assert observation is not None
    assert observation.instant == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)


def test_u_obs_04_future_values_warn_only_beyond_the_tolerance() -> None:
    exactly_at_tolerance = NOW + FUTURE_SKEW_TOLERANCE
    _, warnings = normalize_observed(RawObservation(exactly_at_tolerance), None, NOW)
    assert warnings == []

    # One second past the tolerance still renders as "5m": durations floor to minutes.
    just_beyond = NOW + FUTURE_SKEW_TOLERANCE + timedelta(seconds=1)
    _, warnings = normalize_observed(RawObservation(just_beyond), None, NOW)
    assert len(warnings) == 1
    assert warnings[0].code == "W003"
    assert warnings[0].message == (
        "observed timestamp 2026-09-28T16:35:01Z is 5m after now; check observed_timezone "
        "or clock skew"
    )

    far_future = NOW + timedelta(hours=1, minutes=45)
    _, warnings = normalize_observed(RawObservation(far_future), None, NOW)
    assert warnings[0].message == (
        "observed timestamp 2026-09-28T18:15:00Z is 1h 45m after now; check "
        "observed_timezone or clock skew"
    )


def test_u_obs_05_aware_value_with_a_configured_zone_warns() -> None:
    aware = datetime(2026, 9, 28, 18, 30, tzinfo=timezone(timedelta(hours=2)))
    observation, warnings = normalize_observed(RawObservation(aware), BERLIN, NOW)
    assert observation is not None
    assert observation.instant == datetime(2026, 9, 28, 16, 30, tzinfo=UTC)
    assert observation.was_naive is False
    assert observation.interpreted_timezone is None
    assert len(warnings) == 1
    assert warnings[0].code == "W002"
    assert warnings[0].message == (
        "observed_timezone 'Europe/Berlin' ignored because the value is timezone-aware"
    )

    # The same value without a configured zone: no warning at all.
    _, warnings = normalize_observed(RawObservation(aware), None, NOW)
    assert warnings == []


def test_naive_now_raises_value_error() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        normalize_observed(RawObservation(None), None, datetime(2026, 9, 28, 16, 30))
