# 0008. Read-only DuckDB and PostgreSQL adapters in v0.1

## Status

Accepted.

## Date

2026-09-29.

## Context

FreshCal's whole output is one scalar per source: `max(loaded_at_field)` over a
relation, optionally filtered. Supporting many warehouses multiplies the surface that
must be tested against real servers while adding no semantics. DuckDB is the natural
first target — a file database with no server, so the quickstart needs nothing
installed — and PostgreSQL is where most production data lives. Both have mature
Python clients whose failure modes differ in ways that matter: DuckDB needs `pytz` to
fetch `TIMESTAMPTZ` and defaults its session zone to the machine's; psycopg needs
`LiteralString` care when composing SQL and honours `statement_timeout`.

## Decision

v0.1 ships two adapters, DuckDB and PostgreSQL, both read-only, both implementing one
`FreshnessReader` contract and passing one shared contract test suite
(§7.1–§7.3). DuckDB opens the database `read_only=True`
(`:memory:` excepted, since DuckDB refuses read-only in-memory databases) and sets
`TimeZone='UTC'` and `file_search_path`; PostgreSQL opens a transaction per read,
sets `LOCAL TIME ZONE 'UTC'` and `statement_timeout`, and sets `read_only=True`.
Values are normalized at the boundary: `TIMESTAMPTZ` to aware UTC, `TIMESTAMP`
returned naive (the core then demands `observed_timezone`, E214), `NULL` to
`RawObservation(None)`, and anything else (a `date`, a string, a number) to E503. The
optional dependencies live in the `duckdb` and `postgres` extras; a missing one is
E505, not an import crash.

## Consequences

The verdict engine sees exactly three shapes of value and nothing adapter-specific,
which is what makes the golden tests meaningful. Every adapter behavior is pinned by
integration tests against real engines (DuckDB always; PostgreSQL under
`FRESHCAL_TEST_PG_DSN`) including the read-only guarantee, the session-zone
independence, and the statement timeout. The cost is two code paths with different
error taxonomies mapped onto one (`E501`–`E505`), and users of other warehouses wait. SQL fragments from the config are trusted input, documented
in `SECURITY.md` and §7.4.

## Alternatives considered

- **SQLAlchemy or a generic DB-API layer.** Rejected: it would hide the very
  differences that produce wrong timestamps (session zone, naive versus aware) and add
  a large dependency for two queries.
- **`dbt`'s adapter ecosystem (run freshness through dbt).** Rejected: requires dbt
  and a project, and gives no control over the normalization rules.
- **DuckDB only in v0.1.** Rejected: PostgreSQL is what most users actually monitor,
  and the contract suite keeps the marginal cost bounded.
- **Read-write connections.** Rejected: a freshness check that can modify the
  warehouse is an unacceptable failure mode; read-only is the cheapest guarantee.
