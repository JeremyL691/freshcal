"""PostgreSQL freshness reader.

The adapter's job is to make one warehouse fact boring: whatever the server's default
time zone, whatever the column type, the core receives either a naive timestamp, an
aware UTC one, or ``None``. It uses psycopg 3 in a read-only transaction per read, with
``SET LOCAL TIME ZONE 'UTC'`` and a statement timeout, and it never commits.

Three guarantees shape the code:

- **Secrets never reach a message.** The DSN is parsed with
  ``psycopg.conninfo.conninfo_to_dict`` before connecting; a parse error raises ``E501``
  with a fixed message that names the environment variable and contains no DSN text.
  Every driver message that becomes ``E501``/``E502`` has the raw DSN, the password and
  the password's percent-encoded forms replaced.
- **One statement, read-only.** The user query goes through the extended protocol
  (``cursor.execute(query, prepare=True)``), so the server rejects a fragment holding
  several statements, and the session is opened with
  ``-c default_transaction_read_only=on``, so even a smuggled ``COMMIT`` cannot start a
  writable transaction.
- **A lost connection is a per-source error.** A read always attempts ``rollback()``; if
  the rollback fails the connection is marked broken and the failing read and every
  later read raise ``E502``. A driver error never escapes as ``E599``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, quote_plus

from freshcal.core.errors import Issue, QueryError
from freshcal.core.model import FreshnessTarget, RawObservation

__all__ = ["PostgresReader"]

_KIND = "postgres"
_E505 = 'the postgres adapter needs an optional dependency: pip install "freshcal[postgres]"'
_MAX_DETAIL = 300
_CONNECT_TIMEOUT_SECONDS = 10
_READ_ONLY_OPTION = "-c default_transaction_read_only=on"
_REDACTED = "<redacted>"
_BROKEN_DETAIL = "the connection was lost or rolled back badly; the reader is broken"


def _import_psycopg() -> Any:
    """Import the optional dependency, or raise ``E505``."""
    try:
        import psycopg
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise QueryError(Issue("E505", _E505)) from error
    return psycopg


def _first_line(error: BaseException) -> str:
    """The first line of a driver message, at most 300 characters."""
    first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
    return first_line[:_MAX_DETAIL]


def _secret_strings(dsn: str, params: dict[str, str]) -> tuple[str, ...]:
    """Every rendering of the DSN that must never appear in a message.

    The password is stored as given and in the percent-encoded forms libpq may echo
    (``%20``-style and ``+``-style); the raw DSN covers whole-string echoes. Longest
    first, so the DSN is replaced before a shorter fragment of it.
    """
    secrets: set[str] = set()
    if dsn:
        secrets.add(dsn)
    password = params.get("password")
    if password:
        secrets.update((password, quote(password, safe=""), quote_plus(password, safe="")))
    return tuple(sorted((secret for secret in secrets if secret), key=len, reverse=True))


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
        self._statement_timeout_seconds = statement_timeout_seconds
        self._broken = False
        self._secrets: tuple[str, ...] = ()
        if dsn_env is not None:
            dsn = os.environ.get(dsn_env)
            if dsn is None:
                raise QueryError(
                    Issue("E504", f"environment variable {dsn_env} is not set (connection.dsn_env)")
                )
            parse_error = (
                f"DSN in ${dsn_env} could not be parsed; check quoting and percent-encoding"
            )
        else:
            # An empty DSN lets libpq apply its own defaults (PGHOST, PGUSER, ~/.pgpass, …).
            dsn = ""
            parse_error = "libpq's own connection parameters could not be parsed; check quoting"
        try:
            params = psycopg.conninfo.conninfo_to_dict(dsn)
        except Exception:
            # The driver's parse error echoes the DSN, password included; it must not reach
            # any caller, so the message is fixed and the cause is not chained.
            raise QueryError(Issue("E501", parse_error)) from None
        self._secrets = _secret_strings(dsn, params)
        options = params.get("options", "")
        read_only_options = (
            f"{options} {_READ_ONLY_OPTION}".strip() if options else _READ_ONLY_OPTION
        )
        try:
            self._conn = psycopg.connect(
                dsn,
                options=read_only_options,
                autocommit=False,
                connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            )
            self._conn.read_only = True
        except Exception as error:
            raise QueryError(
                Issue("E501", f"cannot connect to {_KIND}: {self._scrub(_first_line(error))}")
            ) from None

    def _scrub(self, text: str) -> str:
        """Replace every known secret rendering in a driver message."""
        for secret in self._secrets:
            text = text.replace(secret, _REDACTED)
        return text

    def _fail(self) -> None:
        """After a failed read: roll back, and mark the connection broken if that fails."""
        try:
            self._conn.rollback()
        except Exception:
            self._broken = True

    def _rollback(self) -> None:
        """End the read's transaction; a failed rollback breaks the connection for good."""
        try:
            self._conn.rollback()
        except Exception as error:
            self._broken = True
            raise QueryError(
                Issue("E502", f"query failed: {self._scrub(_first_line(error))}")
            ) from None

    def read_latest(self, target: FreshnessTarget) -> RawObservation:
        """Run the freshness query inside one read-only transaction and roll back."""
        if self._broken:
            raise QueryError(Issue("E502", f"query failed: {_BROKEN_DETAIL}"))
        sql = self._psycopg.sql
        # The fragments below are trusted config input: they come from
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
                # Extended protocol: the server rejects a fragment with several statements
                # ("cannot insert multiple commands into a prepared statement").
                cursor.execute(query, prepare=True)
                row = cursor.fetchone()
            value = row[0] if row is not None else None
        except QueryError:
            raise
        except Exception as error:
            self._fail()
            raise QueryError(
                Issue("E502", f"query failed: {self._scrub(_first_line(error))}")
            ) from error
        self._rollback()  # FreshCal never commits
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
        try:
            self._conn.close()
        except Exception:
            self._broken = True
