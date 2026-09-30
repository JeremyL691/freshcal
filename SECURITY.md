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

For **PostgreSQL**, FreshCal also guarantees single-statement execution: the
freshness query is sent through the extended protocol
(`cursor.execute(query, prepare=True)`), so a fragment such as
`t; CREATE TABLE x(a int)` is rejected by the server ("cannot insert multiple
commands into a prepared statement") before anything runs. The connection is
opened with `-c default_transaction_read_only=on` (plus `read_only = True`), so
every transaction of the session is read-only — including one started after a
`COMMIT` smuggled into a fragment — and a write fails with
`ReadOnlySqlTransaction`. The statement timeout of the read cannot be lifted
from a fragment for the same reason: `; SET LOCAL statement_timeout = 0; …` is
several statements and is rejected before execution.

**DuckDB** has no equivalent statement splitter: `read_only=True` protects the
database file only, and a fragment can still contain several statements
(`relation = "t; COPY …"` runs both). The reader therefore locks its session
down before any query runs, and a fragment cannot lift the lock: every
connection sets, in this order, `allowed_directories` (the config file's
directory and the process working directory), `enable_external_access = false`
and `lock_configuration = true`. After that, `read_csv`/`COPY`/`ATTACH` work
only inside those two directories, and `read_csv('/etc/hosts')`, a `COPY … TO`
or `ATTACH` outside them, `INSTALL` and every later `SET` (including
`SET TimeZone`) fail with a `PermissionException`/`InvalidInputException`,
which becomes the source's `E502`. A config directory whose path contains a
comma cannot be expressed in DuckDB's comma-separated `file_search_path` and is
refused with `E501`. Config files are still trusted code: the lock-down bounds
what a fragment can reach, it does not make an untrusted fragment safe.

### What the read-only guarantees cover

- **PostgreSQL:** every connection is opened with `options='-c
  default_transaction_read_only=on'` and additionally sets `read_only = True`, so
  every transaction is read-only and writes fail with `ReadOnlySqlTransaction`.
  Recommended deployment: a role with `SELECT` only — the read-only transaction is
  a second line of defense, not a substitute.
- **DuckDB:** database files are opened with `read_only=True`, which protects the
  database file, and the session lock-down described above restricts file access
  to the config directory and the working directory. Neither stops a fragment
  from writing a file inside those two directories, so config files must still be
  trusted.

### Timestamps, time zones, and secrets

- FreshCal never writes to the warehouse: no `INSERT`, `UPDATE`, `DELETE`,
  `CREATE`, or DDL is ever issued.
- A DSN is never stored in a config file. Only the *name* of an environment
  variable may be configured (`connection.dsn_env`). The DSN is parsed before
  connecting; a malformed DSN raises `E501` with a fixed message that names the
  variable and contains no DSN text. FreshCal replaces the raw DSN, the password
  and the password's percent-encoded forms in every driver message that becomes
  `E501` or `E502`, so a password cannot leak through a connection or query
  error.
- Statements are bounded: PostgreSQL gets `statement_timeout` (default 30s) on
  every read, and the timeout cannot be lifted by a query fragment.
  A lost connection is a per-source `E502`: the reader marks the connection
  broken and the failing read and every later read fail with `E502` instead of
  aborting the run with an internal error (`E599`). DuckDB has no statement
  timeout in v0.1; this is a documented limitation.
- A warehouse value that cannot be a load time is that source's `E502`, never a
  crash and never a verdict: DuckDB's `±infinity` sentinel (`datetime.max` /
  `datetime.min`, for `TIMESTAMP` and `TIMESTAMPTZ` alike) is refused as an
  infinite timestamp, and an overflow while interpreting a value in its
  configured zone (for example `9999-12-31 22:00` read as `America/New_York`) is
  refused naming the value. The remaining sources of the run are still
  evaluated.

## Scope

Out of scope for this policy: the accuracy of holiday data from the `holidays`
library (report it upstream or use calendar overrides), the correctness of
verdicts for sources you configured incorrectly (see `docs/semantics.md`), and
anything that requires an attacker to already control your config files or
warehouse credentials.
