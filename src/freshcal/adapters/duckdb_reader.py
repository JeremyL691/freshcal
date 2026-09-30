"""DuckDB freshness reader.

Two DuckDB behaviours shape this adapter, both verified with duckdb 1.5.6:

- fetching a ``TIMESTAMP WITH TIME ZONE`` value needs ``pytz`` (the client raises
  "Required module 'pytz' failed to import" without it), and the returned ``tzinfo`` is
  a pytz object — so the value is converted to stdlib UTC before the core sees it;
- the session's ``TimeZone`` defaults to the machine's zone, so every connection sets
  ``TimeZone = 'UTC'`` explicitly; otherwise the same stored instant would be read
  differently on different machines.

``file_search_path`` is set to the config file's directory so that a relation such as
``read_csv('fx_rates.csv')`` resolves the same way every relative path in a config file
does. Database files are opened read-only; ``:memory:`` is the exception, because DuckDB
refuses read-only in-memory databases and an in-memory database has nothing to protect.

The session then locks itself down (audit CFG-17): ``allowed_directories`` admits the
config directory and the process working directory, ``enable_external_access = false``
refuses every other file, extension and ``ATTACH``, and ``lock_configuration = true``
stops a query fragment from lifting any of it (``SET TimeZone`` included). The settings
must be applied in that order and can only be applied once per connection: DuckDB
rejects ``allowed_directories`` changes after ``enable_external_access = false``, and a
second ``_configure_session`` on the same connection detects the existing lock and does
nothing. A config directory whose path contains a comma cannot be expressed in
``file_search_path`` (a comma-separated list) and is refused with ``E501``.

DuckDB's driver represents SQL ``infinity`` (both ``TIMESTAMP`` and ``TIMESTAMPTZ``) as
``datetime.max`` and ``-infinity`` as ``datetime.min``. Those are sentinels, not load
times, and are refused with ``E502`` — the same outcome as PostgreSQL, whose driver
raises for them (audit CFG-15/E2E-03).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from freshcal.core.errors import Issue, QueryError
from freshcal.core.model import FreshnessTarget, RawObservation

__all__ = ["DuckDBReader"]

_KIND = "duckdb"
_E505 = 'the duckdb adapter needs an optional dependency: pip install "freshcal[duckdb]"'
_MAX_DETAIL = 300


def _import_duckdb() -> Any:
    """Import the optional dependency, or raise ``E505``."""
    try:
        import duckdb
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise QueryError(Issue("E505", _E505)) from error
    return duckdb


def _detail(error: Exception) -> str:
    """The first line of a driver message, at most 300 characters."""
    first_line = str(error).splitlines()[0] if str(error) else type(error).__name__
    return first_line[:_MAX_DETAIL]


def _quote_path(path: Path) -> str:
    """A single-quoted SQL string literal for a path."""
    return str(path).replace("'", "''")


def _is_infinite(value: datetime) -> bool:
    """True for the driver's ``±infinity`` sentinel.

    DuckDB returns ``datetime.max`` for ``infinity`` and ``datetime.min`` for
    ``-infinity``, for ``TIMESTAMP`` and ``TIMESTAMPTZ`` alike (verified with 1.5.6); a
    ``TIMESTAMPTZ`` sentinel arrives without a ``tzinfo``. An aware value whose UTC
    conversion overflows is the same boundary and counts as infinite too.
    """
    if value.tzinfo is not None and value.utcoffset() is not None:
        try:
            value = value.astimezone(UTC)
        except (OverflowError, ValueError):
            return True
    return value.replace(tzinfo=None) in (datetime.min, datetime.max)


def _refuse_comma_directory(directory: Path) -> None:
    """``file_search_path`` is a comma-separated list, so a comma cannot be expressed."""
    if "," in str(directory):
        raise QueryError(
            Issue(
                "E501",
                f"cannot connect to {_KIND}: the config directory path contains ',' "
                "which DuckDB's file_search_path would read as a list separator: "
                f"{directory}",
            )
        )


class DuckDBReader:
    """A ``FreshnessReader`` over a DuckDB database file or an in-memory database."""

    def __init__(self, path: str | Path, *, config_dir: Path | None = None) -> None:
        directory = Path(config_dir) if config_dir is not None else Path.cwd()
        _refuse_comma_directory(directory)
        duckdb = _import_duckdb()
        resolved = str(path)
        try:
            if resolved == ":memory:":
                self._conn = duckdb.connect(":memory:")
            else:
                self._conn = duckdb.connect(resolved, read_only=True)
        except Exception as error:
            raise QueryError(
                Issue("E501", f"cannot connect to {_KIND}: {_detail(error)}")
            ) from error
        self._config_dir = directory
        try:
            self._configure_session()
        except QueryError:
            self._conn.close()  # this reader owns the connection it just failed to secure
            raise

    @classmethod
    def from_connection(cls, connection: Any, config_dir: Path) -> DuckDBReader:
        """Wrap an existing connection (used by tests and by the composition root)."""
        _refuse_comma_directory(Path(config_dir))
        reader = cls.__new__(cls)
        reader._conn = connection
        reader._config_dir = Path(config_dir)
        reader._configure_session()
        return reader

    def _configure_session(self) -> None:
        directory = self._config_dir.resolve()
        try:
            if self._session_is_locked():
                return  # an earlier reader already configured (and locked) this session
            self._conn.execute("SET TimeZone = 'UTC'")
            self._conn.execute(f"SET file_search_path = '{_quote_path(directory)}'")
            self._conn.execute(
                "SET allowed_directories = [{}]".format(
                    ", ".join(
                        f"'{_quote_path(path)}'" for path in (directory, Path.cwd().resolve())
                    )
                )
            )
            self._conn.execute("SET enable_external_access = false")
            self._conn.execute("SET lock_configuration = true")
        except Exception as error:
            raise QueryError(
                Issue("E501", f"cannot connect to {_KIND}: {_detail(error)}")
            ) from error

    def _session_is_locked(self) -> bool:
        """Whether this connection already carries the lock-down of an earlier reader."""
        row = self._conn.execute("SELECT current_setting('lock_configuration')").fetchone()
        return bool(row and row[0])

    def read_latest(self, target: FreshnessTarget) -> RawObservation:
        """``SELECT max(loaded_at_field)`` over the relation, with the optional filter."""
        query = f"SELECT max({target.loaded_at_field}) AS observed FROM {target.relation}"
        if target.filter is not None:
            query += f" WHERE ({target.filter})"
        try:
            rows = self._conn.execute(query).fetchall()
        except Exception as error:
            raise QueryError(Issue("E502", f"query failed: {_detail(error)}")) from error
        value = rows[0][0] if rows else None
        return self._observation(value, target)

    @staticmethod
    def _observation(value: object, target: FreshnessTarget) -> RawObservation:
        if value is None:
            return RawObservation(None)
        if isinstance(value, datetime):
            if _is_infinite(value):
                raise QueryError(
                    Issue(
                        "E502",
                        f"query failed: max({target.loaded_at_field}) returned an infinite "
                        "timestamp",
                    )
                )
            if value.tzinfo is None or value.utcoffset() is None:
                return RawObservation(value)  # naive: the core demands a zone (E214)
            # pytz-based tzinfo from DuckDB -> stdlib UTC, so the core never sees pytz.
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
