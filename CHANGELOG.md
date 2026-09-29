# Changelog

All notable changes to this project are documented in this file. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the version is `0.y.z`, breaking changes bump `y`.

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
- **Configuration.** One YAML file (or none, when only dbt rules are used) validated by a
  committed JSON Schema first and then semantically: unknown fields, missing fields,
  wrong types, bad time zones, invalid cron expressions, bad durations, date conflicts,
  and unknown holiday codes all produce coded messages with precise locations. Secrets
  never belong in the file; only the name of an environment variable holding a DSN may be
  configured.
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

### Notes

- Not published to PyPI: install from a clone (`uv sync --extra duckdb`).
- Windows is not tested in v0.1. Arrival is inferred from load timestamps, so a reload of
  old rows can hide a missing release; grace is wall-clock time. See the README's
  limitations and the roadmap in `BLUEPRINT.md` §15.

## [Unreleased]

### Added

- Repository skeleton: installable `freshcal` package (Python ≥ 3.11), console
  script `freshcal`, `py.typed`, Apache-2.0 license.
- Quality tooling: ruff (with a ban on reading the system clock outside the
  clock adapter), mypy strict, pytest, coverage, import-linter architecture
  contracts, pre-commit.

[0.1.0]: https://github.com/example/freshcal/releases/tag/v0.1.0
