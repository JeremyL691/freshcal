# FreshCal

[![CI](https://github.com/JeremyL691/freshcal/actions/workflows/ci.yml/badge.svg)](https://github.com/JeremyL691/freshcal/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)

**Data freshness checks that know about business days, holidays, and publication
schedules.** Declare *when* data should arrive (cron, business days, Nth or last business
day of the month, holiday calendars, overrides, time zone) and *how late* it may be (a
grace window); FreshCal tells you whether a due release is currently missing.

- **No weekend or holiday false alarms**: Friday's data on Monday morning is on time;
  a missing release on an ordinary business day is not.
- **Real calendars**: country, subdivision and financial-market holidays from the
  [`holidays`](https://pypi.org/project/holidays/) library, plus your own overrides.
- **Explainable verdicts**: `freshcal explain` walks through every release, deadline and
  observation behind a result.
- **Fits your stack**: DuckDB and PostgreSQL, rules inline or in dbt `meta`, a terminal
  table or a versioned JSON report, and exit codes a scheduler or CI job can act on.

> **Status:** early release (v0.1). The semantics are pinned by golden tests and checked
> against an independent reference implementation, but the tool is new: run it next to
> your existing checks before relying on it.

## Why

Fixed-threshold freshness checks raise false alarms on Monday mornings, holidays, and the
day after a TARGET closing day, so teams loosen them — and then miss real delays on
ordinary business days. FreshCal models the schedule the publisher actually follows: the
ECB publishes on TARGET business days at around 16:00 CET, so Friday's data present on
Monday morning is *not* an alert, while a missing 16:00 release is.

## Install

FreshCal is not on PyPI yet; install it straight from GitHub. It needs Python 3.11 or
newer. As a command-line tool with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install "freshcal[duckdb] @ git+https://github.com/JeremyL691/freshcal@v0.1.2"
freshcal --version  # freshcal 0.1.2
```

or with pip, in the environment of your choice:

```bash
pip install "freshcal[duckdb] @ git+https://github.com/JeremyL691/freshcal@v0.1.2"
```

The `duckdb` extra adds DuckDB support and the `postgres` extra adds PostgreSQL
(`freshcal[duckdb,postgres]` for both). Each
[release](https://github.com/JeremyL691/freshcal/releases) also carries the wheel and
the source distribution.

To run the quickstart below, work from a clone:

```bash
git clone https://github.com/JeremyL691/freshcal.git && cd freshcal
uv sync --extra duckdb --no-dev     # or --all-extras --no-dev for PostgreSQL too
uv run freshcal --version
```

## 30-second quickstart

The example config reads a small CSV with DuckDB, applies the ECB's schedule (business
days at 15:45 Europe/Berlin, TARGET holidays, 2 h 15 m grace) and is evaluated three times
on Monday 2026-09-28. `examples/ecb/fx_rates.csv` is **synthetic** sample data shaped like
ECB reference rates, not the real rates. The release time is illustrative and set before
the earliest collected publication proxy (15:56 CEST on both collected days,
`docs/validation.md` section B) — the earliest time a loader could see the data — not from
a distribution; the grace window keeps the 18:00 deadline.

<!-- quickstart:command:1 -->
```bash
uv run freshcal check -c examples/ecb/freshcal.yml --now 2026-09-28T07:30:00+02:00
```
<!-- quickstart:output:1 -->
```text
FreshCal 0.1.1 | evaluated at 2026-09-28T05:30:00Z | 1 source

SOURCE        STATUS   RELEASE                    DEADLINE                   OBSERVED                   NEXT EXPECTED
ecb.fx_rates  ON_TIME  Fri 2026-09-25 15:45 CEST  Fri 2026-09-25 18:00 CEST  Fri 2026-09-25 16:07 CEST  Mon 2026-09-28 15:45 CEST

ecb.fx_rates: On time: latest release Fri 2026-09-25 15:45 CEST arrived (observed Fri 2026-09-25 16:07 CEST); next release Mon 2026-09-28 15:45 CEST.

Summary: 1 ON_TIME | exit code 0
```

Monday morning with Friday's data is **not** an alert.

<!-- quickstart:command:2 -->
```bash
uv run freshcal check -c examples/ecb/freshcal.yml --now 2026-09-28T17:00:00+02:00
```
<!-- quickstart:output:2 -->
```text
FreshCal 0.1.1 | evaluated at 2026-09-28T15:00:00Z | 1 source

SOURCE        STATUS   RELEASE                    DEADLINE                   OBSERVED                   NEXT EXPECTED
ecb.fx_rates  NOT_DUE  Mon 2026-09-28 15:45 CEST  Mon 2026-09-28 18:00 CEST  Fri 2026-09-25 16:07 CEST  Tue 2026-09-29 15:45 CEST

ecb.fx_rates: Not due: release Mon 2026-09-28 15:45 CEST has not arrived yet; grace window ends Mon 2026-09-28 18:00 CEST; next release Tue 2026-09-29 15:45 CEST.

Summary: 1 NOT_DUE | exit code 0
```

At 17:00 the release is still inside its grace window, so nothing is wrong yet.

<!-- quickstart:command:3 -->
```bash
uv run freshcal check -c examples/ecb/freshcal.yml --now 2026-09-28T18:30:00+02:00
```
<!-- quickstart:output:3 -->
```text
FreshCal 0.1.1 | evaluated at 2026-09-28T16:30:00Z | 1 source

SOURCE        STATUS   RELEASE                    DEADLINE                   OBSERVED                   NEXT EXPECTED
ecb.fx_rates  OVERDUE  Mon 2026-09-28 15:45 CEST  Mon 2026-09-28 18:00 CEST  Fri 2026-09-25 16:07 CEST  Tue 2026-09-29 15:45 CEST

ecb.fx_rates: Overdue: release Mon 2026-09-28 15:45 CEST missed its deadline Mon 2026-09-28 18:00 CEST; 1 release missed; latest data observed Fri 2026-09-25 16:07 CEST.

Summary: 1 OVERDUE | exit code 1
```

Half an hour later the deadline has passed and the run exits 1, so a scheduler or CI job
can act on it.

## How it works

1. **Releases** — the schedule and calendar produce the local dates and times a release is
   expected: `business_days` at a configured local time, `monthly_business_day` with
   `business_day: 3` at 09:00 (the Nth business day of the month), or a cron expression.
   Releases are converted to UTC with `fold=0`, so a spring-forward
   wall time is shifted forward and a fall-back wall time keeps its first occurrence.
2. **Deadline** — *D = R + grace*, in elapsed time: a Friday 22:00 release with 6 h grace
   is due Saturday at 04:00, because pipeline latency does not pause on weekends.
3. **Observed** — `max(loaded_at_field)` from the warehouse, converted to UTC.
4. **Verdict** — the first release *R* with *R > observed* decides: if *R* is in the
   future → `ON_TIME`; if *now* is inside *[R, D]* → `NOT_DUE`; if *now > D* →
   `OVERDUE`. `NO_DATA`, `CONFIG_ERROR` and `QUERY_ERROR` answer "the check could not say"
   instead of pretending to be a verdict.

The full algorithm, the decision table, and every edge-case ruling are in
[docs/semantics.md](docs/semantics.md).

## Configuration

```yaml
version: 1
connection: {type: duckdb, path: warehouse.duckdb}
sources:
  - name: ecb.fx_rates
    relation: raw.ecb_fx_rates
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "15:45", timezone: Europe/Berlin}
    calendar:
      weekend: [sat, sun]
      holidays:
        - financial: XECB        # TARGET closing days
    grace: 2h15m
    observed_timezone: UTC       # the loader writes naive UTC timestamps
```

Field reference, defaults, the issue catalog, and the secrets policy are in
[docs/configuration.md](docs/configuration.md).

### PostgreSQL: use a SELECT-only role

Give the FreshCal connection a PostgreSQL role with only `SELECT` on the tables you
check. FreshCal opens the connection with `default_transaction_read_only=on`, executes
the freshness query as a single prepared statement (the server rejects a fragment that
smuggles a second statement), and bounds every read with `statement_timeout` (default
30 s) — but the read-only session is a second line of defense, not a substitute for a
least-privilege role. Keep the DSN in an environment variable and configure only its name
(`connection: {type: postgres, dsn_env: PGDSN}`); FreshCal never stores the DSN, and it
replaces the DSN and its password in every connection or query error it reports.

FreshCal's query is `SELECT max(<loaded_at_field>) FROM <relation> WHERE <filter>`. A
composite index whose leading columns are the ones your `filter` tests and whose last
column is the load timestamp (for example `CREATE INDEX ON raw.ecb_fx_rates (feed,
_loaded_at)` for `filter: "feed = 'ecb'"`) lets PostgreSQL answer it from the index
instead of scanning the table; without one, each check scans. See
[docs/configuration.md](docs/configuration.md) for the full field reference.

## dbt

Add a rule to a source table and point FreshCal at the manifest dbt writes:

```yaml
# models/sources.yml
sources:
  - name: ecb
    schema: raw_ecb
    tables:
      - name: fx_rates
        loaded_at_field: _loaded_at
        config:
          meta:
            freshcal:
              schedule: {kind: business_days, time: "15:45", timezone: Europe/Berlin}
              calendar: target          # defined in freshcal.yml
              grace: 2h15m
              observed_timezone: UTC    # the loader writes naive UTC timestamps
```

```yaml
# freshcal.yml
version: 1
connection: {type: duckdb, path: warehouse.duckdb}
dbt: {manifest: target/manifest.json}
calendars:
  target: {holidays: [{financial: XECB}]}
```

FreshCal reads `manifest.json` schema **v12** and never parses `sources.yml` or renders
Jinja. dbt-core 1.12.5 with dbt-duckdb 1.11.0 is the version tested in this repository;
other dbt releases that declare v12 should work but are untested. `loaded_at_query` is not
supported in v0.1: a source that sets it without `loaded_at_field` reports `E303`. A
source defined both in the config file and in the manifest uses the **config file**
definition and reports `W004`.

## CLI

| Command | Purpose | Exit codes |
|---|---|---|
| `freshcal check` | Evaluate selected sources and print a report | 0, 1, 2, 3 |
| `freshcal next` | List upcoming expected releases | 0, 2, 3 |
| `freshcal explain SOURCE_ID` | Step-by-step reasoning for one source | 0, 1, 2, 3 |
| `freshcal validate` | Validate config and schedules without a warehouse | 0, 2, 3 |

Useful flags: `-c/--config PATH`, `--now INSTANT` (ISO 8601 with an offset; makes runs
reproducible), `-s/--select PATTERN` (repeatable `fnmatch` on the source ID), `--format
{table,json}`, `-o/--output PATH`, `--count N` for `next`, and `--observed VALUE` for
`explain` (an ISO instant, a naive instant, or `null`) so it can run without a warehouse.

Exit codes: **0** every source `ON_TIME` or `NOT_DUE`; **1** at least one `OVERDUE` or
`NO_DATA`; **2** invalid configuration, CLI misuse, or at least one `CONFIG_ERROR`; **3**
at least one `QUERY_ERROR` (including connection failures) or an internal error.
Precedence is `2 > 3 > 1 > 0`.

JSON reports are versioned (`schema_version: "1.0"`) and validated against a committed
schema, so they are safe to consume from a script.

## How FreshCal relates to other tools

*Checked 2026-09-29.*

- **dbt source freshness** configures `warn_after` / `error_after` as fixed durations
  (`count` + `period` of `minute`, `hour`, or `day`) with `loaded_at_field`,
  `loaded_at_query`, and `filter` (docs.getdbt.com/reference/resource-properties/freshness,
  checked 2026-09-29). Its only documented Jinja workaround applies to `build_after`
  (custom build frequency, for example a different count on weekends), not to the
  freshness thresholds; there is no documented native holiday-calendar, business-day,
  cron, or non-fixed-threshold support. The request for time-aware freshness checks, dbt
  issue #10963 ("[Feature] Time Aware Freshness Checks", labels `type:feature`,
  `Refinement`, `freshness`, `engine:v1`), is **open** as of 2026-09-29 (GitHub API on
  `dbt-labs/dbt`, last updated 2026-06-01). FreshCal can be run next to dbt and reads the
  same `loaded_at_field` convention.
- **Elementary** offers statistical freshness monitoring (`freshness_anomalies`) and a
  rule-based daily SLA test, `data_freshness_sla`, whose parameters are `timestamp_column`,
  `sla_time`, `timezone`, optional `day_of_week`, `day_of_month`, and `where_expression`
  (macro source in `elementary-data/dbt-data-reliability`, checked 2026-09-29). It models
  a single daily deadline with weekday/month-day filters; it does not model holiday
  calendars, Nth/last business day, roll conventions, grace windows across releases, or
  several missed releases. FreshCal is complementary: explicit business calendars and
  release semantics rather than a daily SLA or a learned baseline.

## What the statuses mean

- `ON_TIME`: judged by load timestamps, no release whose deadline has passed is currently
  missing. It does **not** mean past releases were punctual, and a reload of old rows can
  hide a missing release.
- `NOT_DUE`: at least one release has occurred and is missing, but all missing releases are
  still inside their grace windows.
- `OVERDUE`: at least one release is missing after its deadline.

`ON_TIME` is a current-state verdict, not a punctuality record.

## Validation

FreshCal's calendar model is checked against real publication histories: the ECB's euro
reference rates for every publication day from 1999-01-04 to 2026-09-28 (7,102 dates) with
the `financial: XECB` calendar match exactly, and the US Treasury daily par yield curve for
2023–2025 (749 dates) matches exactly once two Good Fridays are added as non-working days
and Veterans Day 2023-11-10 as a working day. Arrival-time validation has not been done: the
ECB publication-time proxy has too few collected business days (2 of the 20 needed), so no
statement about arrival times is made. Details, including the disagreements that library
calendars produce and the replay's "insufficient data" report, are in
[docs/validation.md](docs/validation.md).

Public sources say nothing about your own pipelines: run FreshCal in **shadow mode** next
to your existing checks until you have seen it agree with reality for a few weeks.

## Limitations

- **Load-time semantics.** Arrival is inferred from `max(loaded_at_field)`, so a backfill
  or a loader that touches `loaded_at` on every run can make a missing release look
  arrived, and a release that arrived late but is present now is `ON_TIME`. Per-period
  checks (`rate_date = release date`) are a roadmap item.
- **A publication earlier than the release time never counts.** Arrival is `O ≥ R`, so
  data loaded *before* the release time does not satisfy it: if the schedule time is later
  than the real earliest publication, FreshCal reports the release missing — `OVERDUE`
  once the deadline has passed — until the loader runs again at or after `R`. On
  2026-09-28 the ECB's daily file was observably available at 15:56:44 CEST (the
  `Last-Modified` proxy, not proof of publication; `docs/validation.md` section B), before
  the 16:00 a naive rule would declare. Set `time` to the earliest time your loader can see
  the data (the example uses 15:45 with a 2 h 15 m grace window, keeping the 18:00
  deadline).
- **Grace is wall-clock time**, not business time; a Friday 22:00 release with 6 h grace is
  due Saturday 04:00.
- **One instant is one delivery obligation**: releases that roll onto the same instant
  (a weekend's daily files arriving together on Monday) count once.
- **One release per wall time in a DST overlap**: during a fall-back hour, cron schedules
  do not fire twice.
- **Holiday data comes from the `holidays` library** plus your overrides. Calendars whose
  dates are announced yearly should set `valid_until`; evaluating past it stops with
  `E408`.
- **Naive timestamps need `observed_timezone`**; without it the result is `E214` — a
  configuration error, never a guess.
- **DuckDB and PostgreSQL only**, and DuckDB has no statement timeout in v0.1.
- Tested on macOS with Python 3.11 (full suite); the CI workflow targets Linux and
  Python 3.11-3.14 but has not run yet. Windows is not tested in v0.1.

## Roadmap

Planned (not implemented): period-column freshness (per-period arrival checks),
punctuality audits, Slack-formatted output, Elementary integration, more warehouses
(Snowflake, BigQuery, Databricks, Redshift), business-time grace windows, importing
`holidays` make-up workdays, `loaded_at_query` support, more manifest versions, Python
3.15, upstream contributions to dbt and Elementary, and PyPI publishing via trusted
publishing. Each item starts with an ADR ([docs/adr/](docs/adr/README.md)).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the four check commands, the test
layout, and how to propose calendar or semantics changes. Security issues:
[SECURITY.md](SECURITY.md). Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

Apache-2.0; see [LICENSE](LICENSE).
