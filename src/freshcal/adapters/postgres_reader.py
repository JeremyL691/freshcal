"""PostgreSQL freshness reader (BLUEPRINT.md §7.2).

The adapter's job is to make one warehouse fact boring: whatever the server's default
time zone, whatever the column type, the core receives either a naive timestamp, an
aware UTC one, or ``None``. It uses psycopg 3 in a read-only transaction per read, with
``SET LOCAL TIME ZONE 'UTC'`` and a statement timeout, and it never commits.

Secrets never appear here: the DSN comes from an environment variable whose *name* the
config file holds, and it is never logged, stored in a report, or appended to an error
message (psycopg's own messages do not include the password).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from freshcal.core.errors import Issue, QueryError
from freshcal.core.model import FreshnessTarget, RawObservation

__all__ = ["PostgresReader"]

_KIND = "postgres"
_E505 = 'the postgres adapter needs an optional dependency: pip install "freshcal[postgres]"'
_MAX_DETAIL = 300
_CONNECT_TIMEOUT_SECONDS = 10


def _import_psycopg() -> Any:
    """Import the optional dependency, or raise ``E505``."""
    try:
        import psycopg
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise QueryError(Issue("E505", _E505)) from error
    return psycopg


def _detail(error: Exception) -> str:
    """The first line of a driver message, at most 300 characters."""
    first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
    return first_line[:_MAX_DETAIL]


class PostgresReader:
    """A ``FreshnessReader`` over a PostgreSQL database."""

    def __init__(
        self,
        *,
        dsn_env: str | None = None,
        statement_timeout_seconds: int = 30,
    ) -> None:
        psycopg = _import_psycopg()
        self._psycopg = psycopg
        if dsn_env is not None:
            dsn = os.environ.get(dsn_env)
            if dsn is None:
                raise QueryError(
                    Issue("E504", f"environment variable {dsn_env} is not set (connection.dsn_env)")
                )
        else:
            # An empty DSN lets libpq apply its own defaults (PGHOST, PGUSER, ~/.pgpass, …).
            dsn = ""
        self._statement_timeout_seconds = statement_timeout_seconds
        try:
            self._conn = psycopg.connect(
                dsn, autocommit=False, connect_timeout=_CONNECT_TIMEOUT_SECONDS
            )
            self._conn.read_only = True
        except Exception as error:
            raise QueryError(
                Issue("E501", f"cannot connect to {_KIND}: {_detail(error)}")
            ) from error

    def read_latest(self, target: FreshnessTarget) -> RawObservation:
        """Run the freshness query inside one read-only transaction and roll back."""
        sql = self._psycopg.sql
        # The fragments below are trusted config input (BLUEPRINT.md §7.4): they come from
        # files controlled by the people who control the warehouse credentials.
        field = sql.SQL(target.loaded_at_field)
        relation = sql.SQL(target.relation)
        if target.filter is None:
            query = sql.SQL("SELECT max({field}) AS observed FROM {relation}").format(
                field=field, relation=relation
            )
        else:
            query = sql.SQL(
                "SELECT max({field}) AS observed FROM {relation} WHERE ({filter})"
            ).format(
                field=field,
                relation=relation,
                filter=sql.SQL(target.filter),
            )

        try:
            with self._conn.cursor() as cursor:
                cursor.execute(sql.SQL("SET LOCAL TIME ZONE 'UTC'"))
                cursor.execute(
                    sql.SQL("SET LOCAL statement_timeout = {timeout}").format(
                        timeout=sql.Literal(f"{self._statement_timeout_seconds}s")
                    )
                )
                cursor.execute(query)
                row = cursor.fetchone()
            value = row[0] if row is not None else None
        except self._psycopg.Error as error:
            self._conn.rollback()
            raise QueryError(Issue("E502", f"query failed: {_detail(error)}")) from error
        self._conn.rollback()  # FreshCal never commits
        return self._observation(value, target)

    @staticmethod
    def _observation(value: object, target: FreshnessTarget) -> RawObservation:
        if value is None:
            return RawObservation(None)
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                return RawObservation(value)  # naive: the core demands a zone (E214)
            return RawObservation(value.astimezone(UTC))
        raise QueryError(
            Issue(
                "E503",
                f"max({target.loaded_at_field}) returned {type(value).__name__}; "
                "expected TIMESTAMP or TIMESTAMPTZ",
            )
        )

    def close(self) -> None:
        self._conn.close()
