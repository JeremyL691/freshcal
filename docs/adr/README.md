# Architecture decision records

Each record states one decision, its context, its consequences, and the alternatives that
were rejected. A changed decision gets a new record that supersedes the old one. Section
numbers written as `§n` inside a record refer to FreshCal's internal design specification,
which is not published; the user-facing descriptions are [../semantics.md](../semantics.md)
and [../configuration.md](../configuration.md).

| Record | Status |
|---|---|
| [0001 Record architecture decisions](0001-record-architecture-decisions.md) | Accepted |
| [0002 Standalone project, not a fork of dbt or Elementary](0002-standalone-project.md) | Accepted |
| [0003 Hexagonal architecture with a pure core](0003-hexagonal-architecture.md) | Accepted |
| [0004 Python 3.11 or newer with mypy strict](0004-python-version-and-typing.md) | Accepted |
| [0005 Reuse holidays, croniter, and zoneinfo for calendar primitives](0005-reuse-calendar-primitives.md) | Accepted |
| [0006 Calendar overrides and fully custom calendars](0006-calendar-overrides.md) | Accepted |
| [0007 dbt integration through manifest.json v12 and meta.freshcal](0007-dbt-manifest-integration.md) | Accepted |
| [0008 Read-only DuckDB and PostgreSQL adapters in v0.1](0008-warehouse-adapters.md) | Accepted |
| [0009 Terminal table and versioned JSON report](0009-output-formats.md) | Accepted |
| [0010 Injectable clock, UTC-only core, and explicit zones for naive timestamps](0010-injectable-clock-and-utc-core.md) | Accepted |
| [0011 Load-time arrival semantics; period-column mode deferred](0011-load-time-semantics.md) | Accepted |
| [0012 DST resolution with fold=0](0012-dst-resolution.md) | Accepted |
| [0013 Wall-clock grace windows](0013-wall-clock-grace.md) | Accepted |
| [0014 Cron non-business-day default none; clamping for Nth business day](0014-cron-policy-and-clamping.md) | Accepted |
| [0015 Verdicts, non-verdict outcomes, and exit codes](0015-verdicts-and-exit-codes.md) | Accepted |
| [0016 Tooling: uv, hatchling, ruff, mypy, import-linter, argparse, jsonschema](0016-tooling.md) | Accepted |
| [0017 License choice](0017-license.md) | Accepted |
| [0018 Real-data validation against public publication histories](0018-real-data-validation.md) | Accepted |
| [0020 DST-gap resolution: the transition instant instead of the gap length](0020-dst-gap-resolution.md) | Deferred |

New records start from [0000-template.md](0000-template.md).
