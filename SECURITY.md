# Security Policy

## Supported versions

Only the latest release is supported. FreshCal v0.1 is not published to PyPI;
the supported way to run it is from a clone of this repository.

## Reporting a vulnerability

Report suspected vulnerabilities through **GitHub private vulnerability
reporting** (the "Report a vulnerability" button on the repository's Security
tab). Please do not open a public issue, and do not include working exploits in
public discussion until a fix is available.

Include the FreshCal version (or commit), the platform, the configuration, and
the smallest input that shows the problem.

## What FreshCal does and does not protect against

### Configuration files are code

`relation`, `loaded_at_field`, and `filter` are SQL fragments inserted verbatim
into the freshness query, exactly as dbt inserts `loaded_at_field` and
`filter`. They are trusted input: whoever can edit a FreshCal config or a dbt
`meta.freshcal` block can already choose what FreshCal reads from the
warehouse. Treat configuration files as code and review them like code.

FreshCal does **not** claim single-statement execution. A fragment such as
`t; CREATE TABLE x(a int)` is sent to the server as written; what stops the
write is the read-only transaction (PostgreSQL) or the read-only database file
(DuckDB), not statement parsing.

### What the read-only guarantees cover

- **PostgreSQL:** every connection sets `read_only = True`, so writes fail with
  `ReadOnlySqlTransaction`. Recommended deployment: a role with `SELECT` only —
  the read-only transaction is a second line of defense, not a substitute.
- **DuckDB:** database files are opened with `read_only=True`, which protects
  the database file only. DuckDB can still read and write local files from any
  connection (for example with `COPY ... TO`), including `:memory:`, which is
  why config files must be trusted.

### Timestamps, time zones, and secrets

- FreshCal never writes to the warehouse: no `INSERT`, `UPDATE`, `DELETE`,
  `CREATE`, or DDL is ever issued.
- A DSN is never stored in a config file. Only the *name* of an environment
  variable may be configured (`connection.dsn_env`), and FreshCal does not
  print DSNs or passwords in output, logs, or error messages.
- Statements are bounded: PostgreSQL gets `statement_timeout` (default 30s).
  DuckDB has no statement timeout in v0.1; this is a documented limitation.

## Scope

Out of scope for this policy: the accuracy of holiday data from the `holidays`
library (report it upstream or use calendar overrides), the correctness of
verdicts for sources you configured incorrectly (see `docs/semantics.md`), and
anything that requires an attacker to already control your config files or
warehouse credentials.
