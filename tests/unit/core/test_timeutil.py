"""Tests for the time utilities."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from freshcal.core.errors import ConfigError
from freshcal.core.timeutil import (
    classify_local,
    format_duration,
    format_local,
    format_utc,
    parse_instant,
    resolve_local,
    to_utc,
)

BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")
SANTIAGO = ZoneInfo("America/Santiago")


def test_u_time_01_to_utc() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        to_utc(datetime(2026, 9, 28, 7, 30))
    assert to_utc(datetime(2026, 9, 28, 7, 30, tzinfo=timezone(timedelta(hours=2)))) == datetime(
        2026, 9, 28, 5, 30, tzinfo=UTC
    )
    assert to_utc(datetime(2026, 9, 28, 5, 30, tzinfo=UTC)).tzinfo is UTC


def test_u_time_02_resolve_local_spring_forward() -> None:
    assert resolve_local(datetime(2026, 3, 29, 2, 30), BERLIN) == datetime(
        2026, 3, 29, 1, 30, tzinfo=UTC
    )


def test_u_time_03_resolve_local_fall_back() -> None:
    assert resolve_local(datetime(2026, 10, 25, 2, 30), BERLIN) == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )


def test_u_time_04_classify_local() -> None:
    assert classify_local(datetime(2026, 3, 29, 2, 30), BERLIN) == "gap"
    assert classify_local(datetime(2026, 10, 25, 2, 30), BERLIN) == "ambiguous"
    assert classify_local(datetime(2026, 9, 25, 16, 0), BERLIN) == "normal"
    assert classify_local(datetime(2026, 3, 8, 2, 30), NEW_YORK) == "gap"
    assert classify_local(datetime(2026, 11, 1, 1, 30), NEW_YORK) == "ambiguous"
    assert classify_local(datetime(2026, 9, 25, 16, 0), ZoneInfo("UTC")) == "normal"


def test_u_time_04_classify_local_rejects_aware_and_resolve_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive local"):
        classify_local(datetime(2026, 9, 25, 16, 0, tzinfo=UTC), BERLIN)
    with pytest.raises(ValueError, match="naive local"):
        resolve_local(datetime(2026, 9, 25, 16, 0, tzinfo=UTC), BERLIN)


def test_u_time_05_format_local_and_utc() -> None:
    instant = datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    assert format_local(instant, BERLIN) == "Fri 2026-09-25 16:00 CEST"
    assert format_local(instant, ZoneInfo("UTC")) == "Fri 2026-09-25 14:00 UTC"
    assert (
        format_local(datetime(2026, 1, 5, 14, 0, tzinfo=UTC), BERLIN) == "Mon 2026-01-05 15:00 CET"
    )
    assert format_local(
        datetime(2026, 9, 25, 23, 7, tzinfo=timezone(timedelta(hours=9))), BERLIN
    ) == ("Fri 2026-09-25 16:07 CEST")

    assert format_utc(instant) == "2026-09-25T14:00:00Z"
    assert format_utc(datetime(2026, 9, 25, 14, 0, 0, 123456, tzinfo=UTC)) == (
        "2026-09-25T14:00:00.123456Z"
    )
    assert format_utc(datetime(2026, 9, 25, 16, 0, tzinfo=BERLIN)) == "2026-09-25T14:00:00Z"


def test_u_time_06_format_duration() -> None:
    assert format_duration(timedelta(hours=1, minutes=45)) == "1h 45m"
    assert format_duration(timedelta(days=1, hours=2)) == "1d 2h"
    assert format_duration(timedelta(seconds=30)) == "<1m"
    assert format_duration(timedelta(0)) == "0m"
    assert format_duration(timedelta(days=1)) == "1d"
    assert format_duration(timedelta(minutes=5)) == "5m"
    assert format_duration(timedelta(days=2, minutes=3)) == "2d 3m"


def test_u_time_07_parse_instant() -> None:
    assert parse_instant("2026-09-28T05:30:00Z", flag="--now") == datetime(
        2026, 9, 28, 5, 30, tzinfo=UTC
    )
    assert parse_instant("2026-09-28T07:30:00+02:00", flag="--now") == datetime(
        2026, 9, 28, 5, 30, tzinfo=UTC
    )
    assert parse_instant("2026-09-28T15:00:00+09:00", flag="--observed") == datetime(
        2026, 9, 28, 6, 0, tzinfo=UTC
    )


@pytest.mark.parametrize("text", ["2026-09-28T05:30:00", "2026-09-28", "nonsense", ""])
def test_u_time_07_parse_instant_rejects_naive_and_bad_input(text: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        parse_instant(text, flag="--now")
    issue = excinfo.value.issue
    assert issue.code == "E211"
    assert issue.message == (
        "--now must be an ISO 8601 date-time with a UTC offset, such as "
        f"2026-09-28T07:30:00+02:00 or 2026-09-28T05:30:00Z; got '{text}'"
    )
