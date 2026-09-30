# Configuration reference

FreshCal reads one YAML file (default `./freshcal.yml`, override with `-c/--config`) and,
optionally, rules from a dbt `manifest.json`. This document is the user-facing companion
to BLUEPRINT.md §4.

## Loading rules

- Relative paths inside a config file (`connection.path`, `dbt.manifest`, an override
  `file`) resolve against the **directory containing the config file**, not the working
  directory. The DuckDB adapter also sets DuckDB's `file_search_path` to that directory,
  so `read_csv('fx_rates.csv')` in a `relation` resolves the same way. The adapter then
  locks the session down: DuckDB's `allowed_directories` becomes the config directory and
  the process working directory, `enable_external_access = false` refuses every other
  file, extension and `ATTACH`, and `lock_configuration = true` stops a query fragment
  from changing any of it (including `SET TimeZone`). A config directory whose path
  contains a comma cannot be expressed in DuckDB's comma-separated `file_search_path`
  and is refused with `E501`.
- YAML is read with a safe loader that keeps `2026-01-04` a string (no implicit
  timestamps) and `16:00` a string (no base-60 numbers). Quote times explicitly:
  `time: "16:00"`.
- Validation happens in two stages: the JSON Schema checks the structure of every field,
  then the loader checks meaning — time zones, cron expressions, durations, holiday codes,
  override files, dates, duplicates.
- Errors that cannot be attributed to one source (YAML syntax, top-level fields,
  `connection`, `defaults`, `calendars`, `dbt`, an unreadable manifest) stop the command
  with exit code 2. Errors attributable to one source mark only that source; the others
  are still evaluated and the run still exits 2.
- **Secrets never belong in a config file.** The only thing you may write is the *name* of
  an environment variable holding a DSN (`connection.dsn_env`). Fields such as `dsn`,
  `password`, `url`, `uri`, `conninfo`, and `user` are rejected with `E105`. The DSN is
  parsed before connecting; a malformed DSN reports a fixed `E501` message naming the
  variable, and FreshCal replaces the DSN, the password and its percent-encoded forms in
  every driver message that reaches `E501`/`E502`.

## Top level

| Field | Type | Required | Notes |
|---|---|---|---|
| `version` | `1` | yes | Config format version. |
| `connection` | mapping | for `check`, `next` and `explain` (unless `--observed`) | `duckdb` or `postgres`. Missing → `E213` (exit 2, no report is produced). |
| `dbt.manifest` | path | no | A `manifest.json` (schema v12) to read rules from. |
| `defaults` | mapping | no | `timezone`, `grace`, `calendar`, `observed_timezone` for every source. |
| `calendars` | mapping name → calendar | no | Named calendars, reused by several sources. |
| `sources` | list | no | A config may define only dbt sources. |

## Connection

| Field | Applies to | Notes |
|---|---|---|
| `type` | both | `duckdb` or `postgres`. |
| `path` | DuckDB | Database file, opened **read-only**. `:memory:` is allowed and opened in memory. |
| `dsn_env` | PostgreSQL | Name of an environment variable holding the DSN. Missing variable → `E504`. If unset, libpq's own defaults apply (`PGHOST`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`, `~/.pgpass`, …). |
| `statement_timeout_seconds` | PostgreSQL | 1–3600, default 30. |

FreshCal never writes to the warehouse: DuckDB opens the file read-only, and PostgreSQL
runs every read inside a read-only transaction. For PostgreSQL the connection is opened
with `default_transaction_read_only=on` (the whole session, not only one transaction) and
the freshness query is sent as a single prepared statement, so a `relation` or `filter`
that contains `;` (for example `t; COMMIT; CREATE TABLE x(a int)`) is rejected by the
server before anything runs — a fragment cannot lift the `statement_timeout` either
(`; SET LOCAL statement_timeout = 0; …` is several statements). A lost connection becomes
a per-source `E502` for that and every later read, never a crash. Both adapters set the
session time zone to UTC. `relation`, `loaded_at_field`, and `filter` are SQL fragments
inserted verbatim — treat config files as code and give the FreshCal connection a
`SELECT`-only PostgreSQL role (see [../SECURITY.md](../SECURITY.md) and the README's
[PostgreSQL section](../README.md#postgresql-use-a-select-only-role), which also shows the
composite index that keeps `max(loaded_at_field)` cheap).

DuckDB has no statement splitter, so a fragment can contain several statements; the
lock-down described under "Loading rules" bounds what they can reach (the config
directory and the working directory) and a fragment cannot lift it. A value that cannot
be a load time is that source's `E502`: DuckDB's `±infinity` (`datetime.max` /
`datetime.min`, for `TIMESTAMP` and `TIMESTAMPTZ`) is refused as an infinite timestamp,
and an overflow while interpreting a value in its configured zone (for example
`timestamp '9999-12-31 22:00'` with `observed_timezone: America/New_York`) is refused
naming the value. The other sources of the run are still evaluated.

## Source

| Field | Required | Notes |
|---|---|---|
| `name` | yes | The source ID: `letters_digits.underscores`, for example `ecb.fx_rates`. Unique (`E206`). |
| `relation` | yes | SQL text inserted after `FROM`. |
| `loaded_at_field` | yes | SQL expression inserted inside `max(...)`. |
| `filter` | no | SQL boolean expression inserted as `WHERE (...)`. Restrict it to rows that represent real deliveries. |
| `schedule` | yes | See below. |
| `calendar` | no | A named calendar or an inline one; defaults to `defaults.calendar`, else weekends Sat/Sun. |
| `grace` | yes (here or in `defaults`) | A duration: `90m`, `2h`, `1d6h`, maximum `366d`. |
| `observed_timezone` | for naive columns | The zone in which to interpret a naive `max(loaded_at_field)`. A naive value without a configured zone is `E214`; write `UTC` explicitly when the column stores UTC. |
| `active_from` | no | Local date in the schedule time zone; releases before it do not exist. |

## Schedule

Exactly one kind:

```yaml
schedule: {kind: cron, cron: "0 6 * * *", timezone: Europe/Berlin, on_non_business_day: following}
schedule: {kind: business_days, time: "16:00", timezone: Europe/Berlin}
schedule: {kind: monthly_business_day, business_day: -1, time: "17:00", timezone: America/New_York}
```

| Field | Kinds | Notes |
|---|---|---|
| `timezone` | all | IANA zone (here or in `defaults`). All date arithmetic uses local dates in this zone. |
| `cron` | `cron` | Exactly five fields. Standard day-of-month/day-of-week OR semantics; `L` and `#` are supported; croniter's `H` and `R` extensions are not (`E202`). |
| `on_non_business_day` | `cron` | `none` (default), `skip`, `following`, `preceding`. Only valid for `cron` (`E205`). |
| `time` | `business_days`, `monthly_business_day` | `"HH:MM"` in the schedule zone; quote it. |
| `business_day` | `monthly_business_day` | `1`–`23` for the Nth business day, `-1`–`-23` counted from the end. `0` and `|N| > 23` are rejected (`E204`). If the month has fewer business days, the release is clamped to the last (or first) one and flagged as `clamped`. |

## Calendar

```yaml
calendars:
  cn_workdays:
    weekend: [sat, sun]
    holidays:
      - country: CN
    overrides:
      - file: calendars/cn-2026-makeup-workdays.yml
      - non_working_days: ["2026-06-12"]
    valid_until: "2026-12-31"
```

A business day is decided in this order: an explicit **working day** override, an
explicit **non-working day** override, the weekend set, then the union of the holiday
calendars. A date listed in both override sets is a configuration error (`E403`).

| Field | Notes |
|---|---|
| `weekend` | Any subset of `mon`…`sun`, at most six entries; `[]` means no weekend. |
| `holidays` | Public calendars (`country` + optional `subdivision` and `categories`) or financial markets (`financial: XECB`); validated against the `holidays` library (`E401`, `E402`). |
| `overrides` | Inline lists and/or override files (paths relative to the config file). A missing, unreadable, malformed, or schema-invalid file is `E404`. |
| `valid_until` | The last date whose holidays and overrides a person has checked. Evaluating after it fails with `E408`; consulting later dates warns with `W005` — the notice window is the 34 days after `now` (`CHUNK + DATE_PADDING_DAYS`), declared explicitly, so `check`, `next` and `validate` agree. A plain `cron` with `on_non_business_day: none` never consults the calendar and never warns. Overrides without it warn at load time (`W006`). |

An override file is a small YAML document:

```yaml
description: China 2026 make-up workdays, from the State Council notice; checked 2026-01-05.
working_days: ["2026-09-20", "2026-10-10"]
non_working_days: []
```

## Durations

`^([0-9]+d)?([0-9]+h)?([0-9]+m)?$`, non-empty, at most `366d`. `1d` is 24 elapsed hours,
not "the same local time tomorrow".

## Issues

Every message has a code and a location, and the CLI prints `CODE location: message`
(per-source issues appear inside the report; `validate` prints one issue per line with
its location, and `--output` is opened before any query runs). The catalog is in
BLUEPRINT.md §4.6; the most common ones:

| Code | Meaning |
|---|---|
| `E100`, `E110` | YAML syntax error; config file unreadable (missing, directory, permissions, not UTF-8). |
| `E101`–`E106` | Unknown field, missing field, wrong type, value not allowed, secret in config, bad format/range. |
| `E201`, `E202`, `E203` | Unknown time zone; invalid cron; duration too long. |
| `E204`–`E208` | Bad `business_day`; policy on the wrong kind; duplicate source ID; unknown calendar; required field unresolved. |
| `E209`, `E214`, `E215` | The schedule has no release in a 1830-day window; a naive timestamp without a zone; an undecidable gap. |
| `E213`, `E216`, `E217` | Missing `connection`; no sources to evaluate; a report output file cannot be written. |
| `E301`–`E304` | Manifest version, readability, missing `loaded_at_field`, forbidden `meta.freshcal` field. |
| `E401`–`E408` | Holiday codes, override files, date conflicts, out-of-range data, roll failures, calendar expiry. |
| `E501`–`E505` | Connection, query, value type, `dsn_env`, and optional-dependency failures. |
| `W002`–`W006` | Warnings (never change the exit code): ignored zone, future observation, config/manifest overlap, calendar consulted past `valid_until`, overrides without `valid_until`. |

## dbt integration

Rules live under a source table's `config.meta.freshcal` (dbt ≥ 1.10 style) or
`meta.freshcal` (older style); both appear in the manifest node's `meta`. The rule has the
same fields as a standalone source except `name` and `relation`, which dbt provides
(`E304`). `loaded_at_field` comes from `meta.freshcal.loaded_at_field`, else the node's
`loaded_at_field`; `filter` from `meta.freshcal.filter`, else `freshness.filter`. A node
with `loaded_at_query` and no `loaded_at_field` is `E303`.

dbt merges a source-level `meta` into each table **shallowly**: a table-level `freshcal`
key replaces the source-level one entirely. Share settings with FreshCal `defaults` and
named `calendars` instead. A source defined both in the config file and in the manifest
uses the config file's definition, with warning `W004`.
