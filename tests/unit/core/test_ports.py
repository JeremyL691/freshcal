"""Tests for the protocol ports (BLUEPRINT.md §5.4, CFG-18)."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.unit.adapters.report_examples import example_check_report

from freshcal.adapters.clock import FixedClock, SystemClock
from freshcal.adapters.report_json import JsonReporter
from freshcal.adapters.report_table import TableReporter
from freshcal.core.ports import Clock, Reporter


def test_cfg_18_clock_protocol_covers_the_clock_adapters() -> None:
    """CFG-18: ``Clock`` exists and the clock adapters implement it.

    The annotations are the test: mypy rejects an adapter that does not satisfy the
    protocol, and ruff's TID251 message points at ``freshcal.core.ports.Clock``.
    """
    system: Clock = SystemClock()
    assert system.now().tzinfo is UTC

    fixed: Clock = FixedClock(datetime(2026, 9, 28, 7, 30, tzinfo=UTC))
    assert fixed.now() == datetime(2026, 9, 28, 7, 30, tzinfo=UTC)


def test_cfg_18_reporter_protocol_covers_both_reporters() -> None:
    """CFG-18: ``Reporter`` exists and both renderers implement ``render``."""
    report = example_check_report()
    table: Reporter = TableReporter()
    json_reporter: Reporter = JsonReporter()
    assert table.render(report)
    assert json_reporter.render(report)
