"""DuckDB reader tests: the shared contract plus DuckDB-specific behaviour (§7.1)."""

from __future__ import annotations

import sys
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from tests.integration.reader_contract import CONTRACT_CHECKS

from freshcal.adapters.duckdb_reader import DuckDBReader
from freshcal.adapters.holidays_provider import HolidaysCalendarProvider
from freshcal.config.loader import DuckDBConnection, load_config
from freshcal.core.errors import QueryError
from freshcal.core.model import FreshnessTarget, Status
from freshcal.core.verdict import evaluate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "configs" / "integration"


class DuckDBHarness:
    """A harness over one in-memory connection with unique table names."""

    def __init__(self, connection: object, config_dir: Path) -> None:
        self._connection = connection
        self._config_dir = config_dir
        self._counter = 0

    def reader(self) -> DuckDBReader:
        return DuckDBReader.from_connection(self._connection, self._config_dir)

    def make_target(
        self, column_type: str, rows: Sequence[str | None], *, filter: str | None = None
    ) -> FreshnessTarget:
        self._counter += 1
        table = f"contract_{self._counter}"
        self._connection.execute(f"CREATE TABLE {table} (loaded_at {column_type})")  # type: ignore[attr-defined]
        for row in rows:
            literal = "NULL" if row is None else row
            self._connection.execute(f"INSERT INTO {table} VALUES ({literal})")  # type: ignore[attr-defined]
        return FreshnessTarget(relation=table, loaded_at_field="loaded_at", filter=filter)


@pytest.fixture
def harness() -> Iterator[DuckDBHarness]:
    connection = duckdb.connect(":memory:")
    try:
        yield DuckDBHarness(connection, FIXTURES)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("name", "check"), CONTRACT_CHECKS, ids=[name for name, _ in CONTRACT_CHECKS]
)
def test_i_duck_01_reader_contract(name: str, check: object, harness: DuckDBHarness) -> None:
    check(harness)  # type: ignore[operator]


def test_i_duck_02_file_database_is_read_only(tmp_path: Path) -> None:
    database = tmp_path / "warehouse.duckdb"
    write_connection = duckdb.connect(str(database))
    write_connection.execute("CREATE TABLE t (loaded_at TIMESTAMP)")
    write_connection.execute("INSERT INTO t VALUES (TIMESTAMP '2026-09-25 14:07:00')")
    write_connection.close()

    reader = DuckDBReader(database, config_dir=tmp_path)
    try:
        assert reader.read_latest(
            FreshnessTarget(relation="t", loaded_at_field="loaded_at")
        ).value == (datetime(2026, 9, 25, 14, 7))
        with pytest.raises(duckdb.Error):
            reader._conn.execute("INSERT INTO t VALUES (TIMESTAMP '2026-09-26 00:00:00')")
    finally:
        reader.close()


def test_i_duck_03_session_zone_does_not_leak_into_aware_values() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("SET TimeZone = 'Asia/Tokyo'")
    connection.execute("CREATE TABLE t (loaded_at TIMESTAMPTZ)")
    connection.execute("INSERT INTO t VALUES (TIMESTAMPTZ '2026-09-25 16:07:00+02:00')")
    try:
        reader = DuckDBReader.from_connection(connection, Path("."))
        observation = reader.read_latest(FreshnessTarget(relation="t", loaded_at_field="loaded_at"))
        # The reader sets TimeZone = 'UTC' on its own session and converts to UTC.
        assert observation.value == datetime(2026, 9, 25, 14, 7, tzinfo=UTC)
        assert observation.value is not None
        assert observation.value.tzinfo is UTC
    finally:
        connection.close()


def test_i_duck_04_date_column_is_e503(harness: DuckDBHarness) -> None:
    target = harness.make_target("DATE", ["DATE '2026-09-25'"])
    with pytest.raises(QueryError) as excinfo:
        harness.reader().read_latest(target)
    assert excinfo.value.issue.code == "E503"


def test_i_duck_05_missing_table_is_e502(harness: DuckDBHarness) -> None:
    target = FreshnessTarget(relation="missing_table", loaded_at_field="loaded_at")
    with pytest.raises(QueryError) as excinfo:
        harness.reader().read_latest(target)
    assert excinfo.value.issue.code == "E502"


def test_i_duck_06_csv_is_resolved_through_file_search_path(tmp_path: Path) -> None:
    (tmp_path / "rates.csv").write_text(
        "rate_date,_loaded_at\n2026-09-25,2026-09-25 14:07:00\n", encoding="utf-8"
    )
    connection = duckdb.connect(":memory:")
    try:
        reader = DuckDBReader.from_connection(connection, tmp_path)
        observation = reader.read_latest(
            FreshnessTarget(relation="read_csv('rates.csv')", loaded_at_field="_loaded_at")
        )
        assert observation.value == datetime(2026, 9, 25, 14, 7)
    finally:
        connection.close()


def test_i_duck_07_nonexistent_database_file_is_e501(tmp_path: Path) -> None:
    with pytest.raises(QueryError) as excinfo:
        DuckDBReader(tmp_path / "missing.duckdb", config_dir=tmp_path)
    issue = excinfo.value.issue
    assert issue.code == "E501"
    assert issue.message.startswith("cannot connect to duckdb: ")


def test_i_duck_08_missing_dependency_is_e505(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "duckdb", None)
    with pytest.raises(QueryError) as excinfo:
        DuckDBReader(":memory:")
    issue = excinfo.value.issue
    assert issue.code == "E505"
    assert issue.message == (
        'the duckdb adapter needs an optional dependency: pip install "freshcal[duckdb]"'
    )


def test_i_duck_09_end_to_end_smoke_config_to_verdict() -> None:
    """The first test that crosses configuration, database, and core (G01's data)."""
    config = load_config(FIXTURES / "ecb_smoke.yml")
    assert config.connection == DuckDBConnection(path=":memory:")
    entry = config.entries[0]
    assert entry.rule is not None
    rule = entry.rule

    connection = duckdb.connect(":memory:")
    try:
        reader = DuckDBReader.from_connection(connection, config.directory)
        raw = reader.read_latest(rule.target)
    finally:
        connection.close()

    assert raw.value == datetime(2026, 9, 25, 14, 7)
    result = evaluate(
        rule, raw, datetime(2026, 9, 28, 5, 30, tzinfo=UTC), HolidaysCalendarProvider()
    )
    assert result.status is Status.ON_TIME
    assert result.release is not None
    assert result.release.instant == datetime(2026, 9, 25, 14, 0, tzinfo=UTC)
    assert result.next_expected_arrival == datetime(2026, 9, 28, 14, 0, tzinfo=UTC)


def test_in_memory_path_is_opened_without_read_only(tmp_path: Path) -> None:
    reader = DuckDBReader(":memory:", config_dir=tmp_path)
    try:
        reader._conn.execute("CREATE TABLE t (loaded_at TIMESTAMP)")
        reader._conn.execute("INSERT INTO t VALUES (TIMESTAMP '2026-09-25 14:07:00')")
        observation = reader.read_latest(FreshnessTarget(relation="t", loaded_at_field="loaded_at"))
        assert observation.value == datetime(2026, 9, 25, 14, 7)
    finally:
        reader.close()


def test_filter_is_inserted_verbatim(tmp_path: Path) -> None:
    reader = DuckDBReader(":memory:", config_dir=tmp_path)
    try:
        reader._conn.execute("CREATE TABLE t (loaded_at TIMESTAMP, kind VARCHAR)")
        reader._conn.execute(
            "INSERT INTO t VALUES "
            "(TIMESTAMP '2026-09-20 06:00:00', 'real'), "
            "(TIMESTAMP '2026-09-27 06:00:00', 'heartbeat')"
        )
        observation = reader.read_latest(
            FreshnessTarget(relation="t", loaded_at_field="loaded_at", filter="kind = 'real'")
        )
        assert observation.value == datetime(2026, 9, 20, 6, 0)
    finally:
        reader.close()
