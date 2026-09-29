"""The contract every ``FreshnessReader`` must satisfy (BLUEPRINT.md §7.3).

One suite, run against every adapter: NULL handling, naive values, aware values, filter
application, unsupported value types, and missing relations. Each adapter's test module
provides a harness that creates tables and readers; the checks below are what the
adapters must have in common.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol

import pytest

from freshcal.core.errors import QueryError
from freshcal.core.model import FreshnessTarget, RawObservation
from freshcal.core.ports import FreshnessReader


class ReaderHarness(Protocol):
    """Creates a reader plus freshly created tables for it to read."""

    def reader(self) -> FreshnessReader:
        """A reader over the harness's database."""

    def make_target(
        self, column_type: str, rows: Sequence[str | None], *, filter: str | None = None
    ) -> FreshnessTarget:
        """Create a one-column table (``loaded_at``) holding ``rows`` as SQL literals."""


def check_null_value(harness: ReaderHarness) -> None:
    target = harness.make_target("TIMESTAMP", [None])
    assert harness.reader().read_latest(target) == RawObservation(None)

    # An empty table behaves like an all-NULL one.
    empty = harness.make_target("TIMESTAMP", [])
    assert harness.reader().read_latest(empty) == RawObservation(None)


def check_naive_value(harness: ReaderHarness) -> None:
    target = harness.make_target("TIMESTAMP", ["TIMESTAMP '2026-09-25 14:07:00'"])
    observation = harness.reader().read_latest(target)
    assert observation.value == datetime(2026, 9, 25, 14, 7)
    assert observation.value is not None
    assert observation.value.tzinfo is None


def check_aware_value(harness: ReaderHarness) -> None:
    target = harness.make_target("TIMESTAMPTZ", ["TIMESTAMPTZ '2026-09-25 16:07:00+02:00'"])
    observation = harness.reader().read_latest(target)
    assert observation.value == datetime(2026, 9, 25, 14, 7, tzinfo=UTC)
    assert observation.value is not None
    assert observation.value.tzinfo is not None
    assert observation.value.utcoffset() == timedelta(0)


def check_aware_value_is_utc(harness: ReaderHarness) -> None:
    """Whatever zone the value was written in, the reader hands the core UTC."""
    target = harness.make_target("TIMESTAMPTZ", ["TIMESTAMPTZ '2026-09-25 23:07:00+09:00'"])
    observation = harness.reader().read_latest(target)
    assert observation.value == datetime(2026, 9, 25, 14, 7, tzinfo=UTC)
    assert observation.value is not None
    assert observation.value.tzinfo is UTC


def check_filter_is_applied(harness: ReaderHarness) -> None:
    target = harness.make_target(
        "TIMESTAMP",
        ["TIMESTAMP '2026-09-20 06:00:00'", "TIMESTAMP '2026-09-27 06:00:00'"],
        filter="loaded_at < TIMESTAMP '2026-09-25 00:00:00'",
    )
    observation = harness.reader().read_latest(target)
    assert observation.value == datetime(2026, 9, 20, 6, 0)

    unfiltered = harness.make_target(
        "TIMESTAMP",
        ["TIMESTAMP '2026-09-20 06:00:00'", "TIMESTAMP '2026-09-27 06:00:00'"],
    )
    observation = harness.reader().read_latest(unfiltered)
    assert observation.value == datetime(2026, 9, 27, 6, 0)


def check_filter_matching_nothing_is_null(harness: ReaderHarness) -> None:
    target = harness.make_target(
        "TIMESTAMP",
        ["TIMESTAMP '2026-09-20 06:00:00'"],
        filter="loaded_at > TIMESTAMP '2030-01-01 00:00:00'",
    )
    assert harness.reader().read_latest(target) == RawObservation(None)


def check_unsupported_type_is_e503(harness: ReaderHarness) -> None:
    target = harness.make_target("DATE", ["DATE '2026-09-25'"])
    with pytest.raises(QueryError) as excinfo:
        harness.reader().read_latest(target)
    issue = excinfo.value.issue
    assert issue.code == "E503"
    assert issue.message == ("max(loaded_at) returned date; expected TIMESTAMP or TIMESTAMPTZ")


def check_missing_relation_is_e502(harness: ReaderHarness) -> None:
    target = FreshnessTarget(relation="no_such_relation_anywhere", loaded_at_field="loaded_at")
    with pytest.raises(QueryError) as excinfo:
        harness.reader().read_latest(target)
    assert excinfo.value.issue.code == "E502"
    assert excinfo.value.issue.message.startswith("query failed: ")


def check_close_is_idempotent_after_use(harness: ReaderHarness) -> None:
    reader = harness.reader()
    target = harness.make_target("TIMESTAMP", ["TIMESTAMP '2026-09-25 14:07:00'"])
    assert reader.read_latest(target).value == datetime(2026, 9, 25, 14, 7)
    reader.close()


CONTRACT_CHECKS: tuple[tuple[str, Callable[[ReaderHarness], None]], ...] = (
    ("null-value", check_null_value),
    ("naive-value", check_naive_value),
    ("aware-value", check_aware_value),
    ("aware-value-is-utc", check_aware_value_is_utc),
    ("filter-applied", check_filter_is_applied),
    ("filter-empty-is-null", check_filter_matching_nothing_is_null),
    ("unsupported-type-e503", check_unsupported_type_is_e503),
    ("missing-relation-e502", check_missing_relation_is_e502),
    ("close", check_close_is_idempotent_after_use),
)
