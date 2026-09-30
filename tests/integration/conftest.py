"""PostgreSQL test fixtures.

The tests are deselected by default (``addopts`` contains ``-m "not postgres"``); running
them without ``FRESHCAL_TEST_PG_DSN`` fails with instructions instead of silently
skipping, because a skipped integration test is indistinguishable from a passing one.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest

INSTRUCTIONS = """\
FRESHCAL_TEST_PG_DSN is not set, so the PostgreSQL integration tests cannot run.

Start a throwaway server and point the tests at it, for example:

  TMP=$(mktemp -d)
  initdb -D "$TMP/pg" -U postgres --auth=trust
  pg_ctl -D "$TMP/pg" -o "-p 54329 -c listen_addresses=127.0.0.1 \\
      -c unix_socket_directories=''" start
  FRESHCAL_TEST_PG_DSN=postgresql://postgres@127.0.0.1:54329/postgres \\
      uv run pytest -m postgres
"""


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    dsn = os.environ.get("FRESHCAL_TEST_PG_DSN")
    if not dsn:
        pytest.fail(INSTRUCTIONS, pytrace=False)
    return dsn


@pytest.fixture(scope="session")
def pg_schema(pg_dsn: str) -> Iterator[str]:
    """A throwaway schema, dropped afterwards, so tests never touch public tables."""
    import psycopg

    name = f"freshcal_test_{uuid4().hex}"
    with psycopg.connect(pg_dsn, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{name}"')
    try:
        yield name
    finally:
        with psycopg.connect(pg_dsn, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA "{name}" CASCADE')
