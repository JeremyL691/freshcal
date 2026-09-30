# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version is `0.y.z`, breaking changes bump `y`.

## [Unreleased]

### Documentation

- Prepared the owner-requested M8 execution plan for nine second-audit findings and updated
  active project instructions and progress. Bug fixes remain pending; M0-M7 completion is historical.

## [0.1.1] - 2026-09-29

Audit remediation: the v0.1.0 release could report a missed release as `ON_TIME`, leaked a
DSN password into error output, and made two security promises it did not keep. Every
finding of the independent audit (`review/v0.1.0/`, recorded as Blueprint Amendment A-7) is
closed; `review/v0.1.0/check_fixes.py` reports 34 PASS, 0 FAIL, 0 SKIP.

**Do not use 0.1.0.** It can report a missing release as `ON_TIME` in two realistic cases
and its PostgreSQL read-only guarantees were false (see "Known issues in 0.1.0" below).

### Fixed

- Release searches: a `preceding` roll that moves a release by more than two days
  (Easter, Monday holidays) or a cron time inside a DST gap could be skipped, reporting a
  missed release as `ON_TIME` (findings SEM-01, SEM-02, SEM-06).
- `explain` could name the release *before* the observation as the next one, tagged every
  future release as the next expected arrival, and cited an internal window edge in `E209`
  (CLI-06, CLI-14, SEM-08, SEM-10).
- `validate` reports every schema and calendar error per source, the merge's `W004` and
  `W005`, and a location for each issue (CFG-04, CLI-03, CLI-04).
- Configuration, override-file and manifest read failures, unrepresentable durations, and
  oversized error values are coded errors instead of `E599` or unbounded output
  (CFG-09…CFG-13, CFG-16, CFG-19).
- DuckDB `±infinity` and overflowing observed timestamps are per-source `E502` errors and
  no longer abort the run (E2E-02, E2E-03).
- `check`/`next` without a `connection` are usage errors (E213, exit 2), `explain` on a
  broken rule shows that rule's own errors, and `--help` documents the exit codes
  (CLI-01, CLI-05, CLI-13).

### Security

- PostgreSQL: the DSN password is removed from every error message, the session runs
  read-only, the freshness query is a single prepared statement, so a fragment cannot
  smuggle a second statement or lift the statement timeout, and a lost connection is a
  per-source `E502` instead of `E599` (findings CFG-01…CFG-03).
- DuckDB: sessions refuse external file access and lock their configuration (CFG-17).

### Changed

- The ECB quickstart example declares `time: "15:45"` and `grace: 2h15m` (deadline 18:00):
  the earliest time a loader can see the data, instead of the illustrative 16:00
  (finding CLI-07).

## [0.1.0] - 2026-09-29

First release. FreshCal answers one question per source: right now, is any release whose
deadline has passed still missing, judged by load timestamps?

### Added

- **Schedules and calendars.** Three schedule kinds (`cron`, `business_days`,
  `monthly_business_day` with Nth or Nth-from-last day and clamping), weekend sets,
  public and financial-market holiday calendars from the `holidays` library, and
  calendar overrides (extra working days, extra non-working days, override files,
  fully custom calendars). Calendars may declare `valid_until`; evaluating past it stops
  with `E408`, and merely consulting later dates warns with `W005`.
- **Verdicts.** `ON_TIME`, `NOT_DUE`, `OVERDUE` plus the non-verdict outcomes `NO_DATA`,
  `CONFIG_ERROR`, and `QUERY_ERROR`; the release and deadline the status depends on, the
  next expected arrival, missed/pending counts (with truncation made explicit), warnings,
  and a one-sentence explanation that is part of the JSON contract.
- **Warehouses.** Read-only DuckDB and PostgreSQL adapters behind one `FreshnessReader`
  contract, with session zones forced to UTC and an unsupported column type reported as
  `E503`.
- **dbt integration.** Rules under `meta.freshcal` (or `config.meta.freshcal`) read from
  `manifest.json` schema v12; `loaded_at_field`, `filter`, and `relation` are mapped as
  documented, an unreadable or unsupported manifest is `E301`/`E302`, and a source defined
  in both the config file and the manifest uses the config file definition with `W004`.
- **CLI.** `freshcal check`, `next`, `explain`, and `validate` with `--config`, `--now`,
  `--select`, `--format {table,json}`, `--output`, `--count`, and `--observed`; exit codes
  0/1/2/3 with documented precedence, table and versioned JSON output, and `--version`.
- **Configuration.** One YAML file validated by a committed JSON Schema first and then
  semantically: unknown fields, missing fields, wrong types, bad time zones, invalid cron
  expressions, bad durations, date conflicts, and unknown holiday codes all produce coded
  messages with precise locations. Secrets never belong in the file; only the name of an
  environment variable holding a DSN may be configured.
- **Documentation.** README with a verified quickstart, `docs/configuration.md`,
  `docs/semantics.md` (the decision table and every edge-case ruling), and
  `docs/validation.md`.
- **Validation.** Release dates checked against the ECB's complete publication history
  (7,102 days since 1999) and three years of US Treasury yields (749 days), including the
  disagreements that library calendars produce and the overrides that fix them; an
  arrival-time replay against the ECB `Last-Modified` publication proxy, reported as
  agreement with a proxy and currently "insufficient data" with too few collected days.
- **Quality gates.** ruff (including a ban on reading the system clock outside the clock
  adapter), mypy strict, import-linter architecture contracts, pytest with coverage gates
  (core ≥ 95 %), Hypothesis property tests, 40 golden scenarios run under three process
  time zones, performance budgets, and a CI workflow (lint, tests on Python 3.11-3.14,
  PostgreSQL integration job, build).

### Known issues in 0.1.0

These are the reasons not to use 0.1.0; all of them are fixed in 0.1.1.

- **False `ON_TIME`.** A `preceding` roll that moved a release by more than two days
  (Easter, Monday holidays) and a cron time inside a DST gap could be skipped, so a missing
  release was reported as `ON_TIME` (SEM-01, SEM-02).
- **DSN leak.** A malformed `dsn_env` value was echoed into `E501`, including the password,
  in the table, the JSON report and `explain` (CFG-01).
- **Read-only escape.** The PostgreSQL reader executed the user's query through the simple
  protocol, so a `; COMMIT; …` fragment could write and `SET LOCAL statement_timeout = 0`
  could lift the timeout (CFG-02).
- **`infinity` aborted a whole run.** One DuckDB or PostgreSQL `±infinity` timestamp raised
  `OverflowError` and destroyed the report for every source (E2E-02).
- **Unbacked claims.** `SECURITY.md` and `docs/configuration.md` promised read-only and
  timeout guarantees the code did not provide, and the README claimed Linux testing that had
  never run (CLI-02).

### Notes

- Not published to PyPI: install from a clone (`uv sync --extra duckdb`) or from the built
  wheel (`dist/freshcal-0.1.1-py3-none-any.whl`).
- Windows is not tested in v0.1. Arrival is inferred from load timestamps, so a reload of
  old rows can hide a missing release; grace is wall-clock time. See the README's
  limitations and the roadmap in `BLUEPRINT.md` §15.
