"""DuckDB freshness reader (BLUEPRINT.md §7.1).

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


class DuckDBReader:
    """A ``FreshnessReader`` over a DuckDB database file or an in-memory database."""

    def __init__(self, path: str | Path, *, config_dir: Path | None = None) -> None:
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
        self._config_dir = Path(config_dir) if config_dir is not None else Path.cwd()
        self._configure_session()

    @classmethod
    def from_connection(cls, connection: Any, config_dir: Path) -> DuckDBReader:
        """Wrap an existing connection (used by tests and by the composition root)."""
        reader = cls.__new__(cls)
        reader._conn = connection
        reader._config_dir = Path(config_dir)
        reader._configure_session()
        return reader

    def _configure_session(self) -> None:
        directory = str(self._config_dir).replace("'", "''")
        try:
            self._conn.execute("SET TimeZone = 'UTC'")
            self._conn.execute(f"SET file_search_path = '{directory}'")
        except Exception as error:
            raise QueryError(
                Issue("E501", f"cannot connect to {_KIND}: {_detail(error)}")
            ) from error

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
