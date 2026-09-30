# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version is `0.y.z`, breaking changes bump `y`.

## [Unreleased]

Second audit: nine confirmed findings are fixed, each with a regression test, including a
cron form that could report a missed release as `ON_TIME` and a diagnostic that echoed a
database password.

### Fixed

- A cron expression with a restricted day-of-month **and** an nth-weekday day-of-week
  (`0 9 1 * 1#1`) dropped the day-of-month branch and could report a missed release as
  `ON_TIME`. Both fields are now the standard OR alternatives: the two branches are merged,
  an occurrence both name is emitted once, and a branch that can never fire no longer fails
  the schedule.
- An invalid structured `name` (a list or mapping, including a YAML alias graph) was
  stringified into the source ID and produced a multi-megabyte report; such an entry is now
  named `sources[index]` and valid identifiers are untouched.
- A DSN pasted into `dsn_env` was echoed by the `E106` diagnostic, userinfo and password
  included. The message now carries the field location and the environment-variable-name
  hint only — never the value.
- A closed stdout pipe (`freshcal … | head`) raised `BrokenPipeError` during interpreter
  shutdown, replacing the report's exit code with 120; the report's own code now survives
 .
- A malformed explicit YAML tag (`version: !!int nope`) escaped as `E599`/exit 3; it is now
  `E100` naming the tag, a bounded value and the line and column.
- A duration with a 5000-digit component hit CPython's digit limit and aborted the run;
  it is now a per-source `E203` and the other sources are still evaluated.
- An embedded NUL in `connection.path`, `dbt.manifest`, the config path or an override path
  escaped as `E599`; each boundary now reports `E106`, `E302`, `E110` or `E404` with the
  reason and a control-character-safe rendering.

### Changed

- The full 10 000-case differential campaign runs in a dedicated CI job instead of being
  excluded by the default marker selection, and a guard test proves the workflow's own
  command selects it.
- The differential comparator evaluates the implementation and the independent oracle
  separately for every operation, so a one-sided failure is reported instead of being read
  as agreement, and the oracle matches the nth-weekday grammar with its own field matching
 .

## [0.1.1] - 2026-09-29

Audit remediation: the v0.1.0 release could report a missed release as `ON_TIME`, leaked a
DSN password into error output, and made two security promises it did not keep. Every
finding of an independent audit is closed, each with a regression test.

**Do not use 0.1.0.** It can report a missing release as `ON_TIME` in two realistic cases
and its PostgreSQL read-only guarantees were false (see "Known issues in 0.1.0" below).

### Fixed

- Release searches: a `preceding` roll that moves a release by more than two days
  (Easter, Monday holidays) or a cron time inside a DST gap could be skipped, reporting a
  missed release as `ON_TIME`.
- `explain` could name the release *before* the observation as the next one, tagged every
  future release as the next expected arrival, and cited an internal window edge in `E209`.
- `validate` reports every schema and calendar error per source, the merge's `W004` and
  `W005`, and a location for each issue.
- Configuration, override-file and manifest read failures, unrepresentable durations, and
  oversized error values are coded errors instead of `E599` or unbounded output.
- DuckDB `±infinity` and overflowing observed timestamps are per-source `E502` errors and
  no longer abort the run.
- `check`/`next` without a `connection` are usage errors (E213, exit 2), `explain` on a
  broken rule shows that rule's own errors, and `--help` documents the exit codes.

### Security

- PostgreSQL: the DSN password is removed from every error message, the session runs
  read-only, the freshness query is a single prepared statement, so a fragment cannot
  smuggle a second statement or lift the statement timeout, and a lost connection is a
  per-source `E502` instead of `E599`.
- DuckDB: sessions refuse external file access and lock their configuration.

### Changed

- The ECB quickstart example declares `time: "15:45"` and `grace: 2h15m` (deadline 18:00):
  the earliest time a loader can see the data, instead of the illustrative 16:00.

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
  release was reported as `ON_TIME`.
- **DSN leak.** A malformed `dsn_env` value was echoed into `E501`, including the password,
  in the table, the JSON report and `explain`.
- **Read-only escape.** The PostgreSQL reader executed the user's query through the simple
  protocol, so a `; COMMIT; …` fragment could write and `SET LOCAL statement_timeout = 0`
  could lift the timeout.
- **`infinity` aborted a whole run.** One DuckDB or PostgreSQL `±infinity` timestamp raised
  `OverflowError` and destroyed the report for every source.
- **Unbacked claims.** `SECURITY.md` and `docs/configuration.md` promised read-only and
  timeout guarantees the code did not provide, and the README claimed Linux testing that had
  never run.

### Notes

- Not published to PyPI: install from a clone (`uv sync --extra duckdb`) or from the built
  wheel (`dist/freshcal-0.1.1-py3-none-any.whl`).
- Windows is not tested in v0.1. Arrival is inferred from load timestamps, so a reload of
  old rows can hide a missing release; grace is wall-clock time. See the README's
  limitations and roadmap.
