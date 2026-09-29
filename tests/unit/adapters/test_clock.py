"""Tests for the clock adapter."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from freshcal.adapters.clock import FixedClock, SystemClock


def test_fixed_clock_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 9, 28, 5, 30))


def test_fixed_clock_converts_to_utc() -> None:
    clock = FixedClock(datetime(2026, 9, 28, 7, 30, tzinfo=timezone(timedelta(hours=2))))
    assert clock.now() == datetime(2026, 9, 28, 5, 30, tzinfo=UTC)


def test_system_clock_is_aware_utc() -> None:
    clock = SystemClock()
    first = clock.now()
    second = clock.now()
    assert first.tzinfo is UTC
    assert second.tzinfo is UTC
    assert second >= first
