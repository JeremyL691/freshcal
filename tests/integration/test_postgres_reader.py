"""PostgreSQL reader tests: the shared contract plus PostgreSQL-specific behaviour (§7.2).

Every test needs ``FRESHCAL_TEST_PG_DSN`` and is marked ``postgres``, so the default
test run deselects it.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest
from tests.integration.reader_contract import CONTRACT_CHECKS

from freshcal import cli
from freshcal.adapters.postgres_reader import PostgresReader
from freshcal.core.errors import QueryError
from freshcal.core.model import FreshnessTarget

pytestmark = pytest.mark.postgres


class PostgresHarness:
    """A harness over one schema, creating a fresh table for every target."""

    def __init__(self, dsn: str, schema: str) -> None:
        self._dsn = dsn
        self._schema = schema
        self._counter = 0
        # The schema is session-scoped, so table names need a per-harness prefix.
        self._prefix = uuid4().hex[:8]

    def reader(self) -> PostgresReader:
        return PostgresReader(dsn_env="FRESHCAL_TEST_PG_DSN")

    def make_target(
        self,
        column_type: str,
        rows: Sequence[str | None],
        *,
        filter: str | None = None,
    ) -> FreshnessTarget:
        import psycopg

        self._counter += 1
        table = f'"{self._schema}".contract_{self._prefix}_{self._counter}'
        with psycopg.connect(self._dsn, autocommit=True) as connection:
            connection.execute(f"CREATE TABLE {table} (loaded_at {column_type})")
            for row in rows:
                literal = "NULL" if row is None else row
                connection.execute(f"INSERT INTO {table} VALUES ({literal})")
        return FreshnessTarget(relation=table, loaded_at_field="loaded_at", filter=filter)


@pytest.fixture
def harness(pg_dsn: str, pg_schema: str) -> PostgresHarness:
    return PostgresHarness(pg_dsn, pg_schema)


@pytest.mark.parametrize(
    ("name", "check"), CONTRACT_CHECKS, ids=[name for name, _ in CONTRACT_CHECKS]
)
def test_i_pg_01_reader_contract(name: str, check: object, harness: PostgresHarness) -> None:
    check(harness)  # type: ignore[operator]


def test_i_pg_02_server_timezone_does_not_leak_into_aware_values(
    harness: PostgresHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DSN that forces the session zone to Asia/Tokyo still yields UTC values."""
    target = harness.make_target("TIMESTAMPTZ", ["TIMESTAMPTZ '2026-09-25 16:07:00+02:00'"])
    tokyo_dsn = f"{os.environ['FRESHCAL_TEST_PG_DSN']}?options={quote('-c timezone=Asia/Tokyo')}"
    monkeypatch.setenv("FRESHCAL_TEST_PG_DSN", tokyo_dsn)

    reader = PostgresReader(dsn_env="FRESHCAL_TEST_PG_DSN")
    try:
        observation = reader.read_latest(target)
        assert observation.value == datetime(2026, 9, 25, 14, 7, tzinfo=UTC)
        assert observation.value is not None
        assert observation.value.tzinfo is UTC
    finally:
        reader.close()


def test_i_pg_02_timestamp_without_zone_stays_naive(harness: PostgresHarness) -> None:
    target = harness.make_target("TIMESTAMP", ["TIMESTAMP '2026-09-25 14:07:00'"])
    reader = harness.reader()
    try:
        observation = reader.read_latest(target)
        assert observation.value == datetime(2026, 9, 25, 14, 7)
        assert observation.value is not None
        assert observation.value.tzinfo is None
    finally:
        reader.close()


def test_i_pg_03_reads_run_in_a_read_only_transaction(harness: PostgresHarness) -> None:
    """The reader's own transaction reports `transaction_read_only = on`."""
    target = FreshnessTarget(
        relation=("(SELECT now() AS x WHERE current_setting('transaction_read_only') = 'on') AS t"),
        loaded_at_field="x",
    )
    reader = harness.reader()
    try:
        observation = reader.read_latest(target)
        assert observation.value is not None, "the read-only transaction was not active"
        assert observation.value.tzinfo is not None
    finally:
        reader.close()


def test_i_pg_03_writes_are_rejected(harness: PostgresHarness) -> None:
    """A write attempt inside the reader's transaction fails with a read-only error."""
    reader = harness.reader()
    try:
        with (
            pytest.raises(reader._psycopg.Error) as excinfo,
            reader._conn.cursor() as cursor,
        ):
            cursor.execute("CREATE TABLE should_not_exist (x int)")
        assert "read-only" in str(excinfo.value).lower()
        reader._conn.rollback()
    finally:
        reader.close()


def test_i_pg_04_statement_timeout_is_e502(harness: PostgresHarness) -> None:
    target = FreshnessTarget(relation="(SELECT pg_sleep(3), now() AS x) AS t", loaded_at_field="x")
    reader = PostgresReader(dsn_env="FRESHCAL_TEST_PG_DSN", statement_timeout_seconds=1)
    try:
        with pytest.raises(QueryError) as excinfo:
            reader.read_latest(target)
        assert excinfo.value.issue.code == "E502"
        assert excinfo.value.issue.message.startswith("query failed: ")
    finally:
        reader.close()


def test_i_pg_05_unreachable_host_is_e501(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRESHCAL_TEST_PG_DSN", "postgresql://postgres@127.0.0.1:1/postgres")
    with pytest.raises(QueryError) as excinfo:
        PostgresReader(dsn_env="FRESHCAL_TEST_PG_DSN")
    issue = excinfo.value.issue
    assert issue.code == "E501"
    assert issue.message.startswith("cannot connect to postgres: ")


def test_i_pg_06_missing_environment_variable_is_e504(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FRESHCAL_MISSING_DSN", raising=False)
    with pytest.raises(QueryError) as excinfo:
        PostgresReader(dsn_env="FRESHCAL_MISSING_DSN")
    issue = excinfo.value.issue
    assert issue.code == "E504"
    assert (
        issue.message == "environment variable FRESHCAL_MISSING_DSN is not set (connection.dsn_env)"
    )


def test_i_pg_07_error_messages_never_contain_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FRESHCAL_TEST_PG_DSN", "postgresql://postgres:s3cr3t-test@127.0.0.1:1/postgres"
    )
    with pytest.raises(QueryError) as excinfo:
        PostgresReader(dsn_env="FRESHCAL_TEST_PG_DSN")
    assert excinfo.value.issue.code == "E501"
    assert "s3cr3t-test" not in excinfo.value.issue.message
    assert "postgres:s3cr3t-test" not in excinfo.value.issue.message


def test_i_pg_07_infinity_timestamp_is_e502(harness: PostgresHarness) -> None:
    target = harness.make_target("TIMESTAMP", ["TIMESTAMP 'infinity'"])
    reader = harness.reader()
    try:
        with pytest.raises(QueryError) as excinfo:
            reader.read_latest(target)
        assert excinfo.value.issue.code == "E502"
    finally:
        reader.close()


def test_i_pg_07_date_column_is_e503(harness: PostgresHarness) -> None:
    target = harness.make_target("DATE", ["DATE '2026-09-25'"])
    reader = harness.reader()
    try:
        with pytest.raises(QueryError) as excinfo:
            reader.read_latest(target)
        issue = excinfo.value.issue
        assert issue.code == "E503"
        assert issue.message == "max(loaded_at) returned date; expected TIMESTAMP or TIMESTAMPTZ"
    finally:
        reader.close()


def test_reader_can_be_reused_for_several_reads(harness: PostgresHarness) -> None:
    first = harness.make_target("TIMESTAMP", ["TIMESTAMP '2026-09-20 06:00:00'"])
    second = harness.make_target("TIMESTAMP", ["TIMESTAMP '2026-09-25 14:07:00'"])
    reader = harness.reader()
    try:
        assert reader.read_latest(first).value == datetime(2026, 9, 20, 6, 0)
        assert reader.read_latest(second).value == datetime(2026, 9, 25, 14, 7)
        assert reader.read_latest(first).value == datetime(2026, 9, 20, 6, 0)
    finally:
        reader.close()


def _admin(dsn: str, sql: str) -> object:
    """Run one statement on a fresh autocommit connection (test-side only)."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as connection:
        cursor = connection.execute(sql)
        return cursor.fetchall() if cursor.description else None


def test_i_pg_09_multi_statement_fragment_is_rejected(
    harness: PostgresHarness, pg_dsn: str, pg_schema: str
) -> None:
    """CFG-02: `; COMMIT; CREATE TABLE …` is rejected and the table is never created."""
    target = harness.make_target("TIMESTAMP", ["TIMESTAMP '2026-09-25 14:07:00'"])
    relation = (
        f'{target.relation}; COMMIT; CREATE TABLE "{pg_schema}".pwn_09(a int); SELECT now() AS x'
    )
    reader = harness.reader()
    try:
        with pytest.raises(QueryError) as excinfo:
            reader.read_latest(FreshnessTarget(relation=relation, loaded_at_field="loaded_at"))
        assert excinfo.value.issue.code == "E502"
    finally:
        reader.close()
    assert _admin(pg_dsn, f"SELECT to_regclass('\"{pg_schema}\".pwn_09')") == [(None,)]


def test_i_pg_10_the_timeout_cannot_be_lifted(harness: PostgresHarness) -> None:
    """CFG-02: `; SET LOCAL statement_timeout = 0; SELECT pg_sleep(3)` fails fast."""
    target = FreshnessTarget(
        relation="(SELECT now() AS x) q; SET LOCAL statement_timeout = 0; SELECT pg_sleep(3)",
        loaded_at_field="x",
    )
    reader = PostgresReader(dsn_env="FRESHCAL_TEST_PG_DSN", statement_timeout_seconds=1)
    started = time.perf_counter()
    try:
        with pytest.raises(QueryError) as excinfo:
            reader.read_latest(target)
    finally:
        reader.close()
    elapsed = time.perf_counter() - started
    assert excinfo.value.issue.code == "E502"
    assert elapsed < 2.0, f"the fragment was executed (took {elapsed:.1f}s)"


def test_i_pg_11_a_lost_connection_is_e502_not_e599(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CFG-03: one source kills its backend; the run still prints a report and exits 3.

    The reader is marked broken and does not silently retry, so the healthy source after
    the dead one is a per-source ``E502`` too — never an ``E599`` for the whole run.
    """
    config = tmp_path / "lost.yml"
    config.write_text(
        "version: 1\n"
        "connection: {type: postgres, dsn_env: FRESHCAL_TEST_PG_DSN, "
        "statement_timeout_seconds: 5}\n"
        "defaults: {grace: 1h, timezone: UTC}\n"
        "sources:\n"
        '  - {name: a.kill, relation: "(SELECT now() AS x, '
        'pg_terminate_backend(pg_backend_pid()) AS k) q", loaded_at_field: x, '
        'schedule: {kind: business_days, time: "16:00"}}\n'
        '  - {name: b.ok, relation: "(SELECT now() AS x) q", loaded_at_field: x, '
        'schedule: {kind: business_days, time: "16:00"}}\n'
    )
    code = cli.main(
        ["check", "--format", "json", "-c", str(config), "--now", "2026-09-28T00:00:00Z"]
    )
    captured = capsys.readouterr()

    assert code == 3
    assert "E599" not in captured.err
    assert captured.err == ""
    report = json.loads(captured.out)
    statuses = {result["source_id"]: result["status"] for result in report["results"]}
    assert statuses == {"a.kill": "QUERY_ERROR", "b.ok": "QUERY_ERROR"}


def test_i_pg_12_an_overflowing_timestamp_is_a_query_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """E2E-02: ``9999-12-31 22:00`` in New York overflows; it is an E502, not an E599."""
    config = tmp_path / "sentinel.yml"
    config.write_text(
        "version: 1\n"
        "connection: {type: postgres, dsn_env: FRESHCAL_TEST_PG_DSN}\n"
        "defaults: {grace: 1h, timezone: UTC}\n"
        "sources:\n"
        "  - {name: b.sentinel, relation: \"(SELECT timestamp '9999-12-31 22:00:00' AS x) q\", "
        "loaded_at_field: x, observed_timezone: America/New_York, "
        "schedule: {kind: business_days, time: '16:00'}}\n"
    )
    code = cli.main(
        ["check", "--format", "json", "-c", str(config), "--now", "2026-09-28T00:00:00Z"]
    )
    captured = capsys.readouterr()

    assert code == 3
    assert "E599" not in captured.err
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report["exit_code"] == 3
    result = report["results"][0]
    assert result["status"] == "QUERY_ERROR"
    assert result["error"]["code"] == "E502"
    assert result["error"]["message"].startswith("query failed: ")
    assert "9999-12-31 22:00:00" in result["error"]["message"]
