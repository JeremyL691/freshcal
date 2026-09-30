# 0002. Standalone project, not a fork of dbt or Elementary

## Status

Accepted.

## Date

2026-09-29.

## Context

The need (business-calendar-aware freshness) sits inside the dbt
and Elementary ecosystem. dbt's source freshness has only fixed `warn_after`/
`error_after` thresholds; issue #10963 ("Time Aware Freshness Checks") is open but
unshipped, and #4450's ECB example is exactly our case (checked 2026-09-28). Elementary offers statistical anomaly detection and a daily
SLA test, not calendar rules. A fork of either would inherit a large release cadence,
their upgrade treadmill, and a plugin surface we do not control.

## Decision

FreshCal is a standalone Python project with its own package, tests, and release. It reads data from a warehouse and prints verdicts; it
does not run inside dbt, does not require dbt to be installed, and depends on no dbt
Python package. Integration with dbt is by reading a produced artifact —
`manifest.json` (ADR 0007) — not by importing dbt code.

## Consequences

FreshCal can be adopted without dbt and cannot be broken by a dbt release; the dbt
integration surface is one declarative file whose schema version is checked
explicitly (E301). We forgo dbt's configuration inheritance and its packaging
ecosystem, and we must document how FreshCal relates to dbt and Elementary ourselves. If dbt ships calendar-aware freshness, FreshCal stays
useful standalone and can reposition rather than being deleted.

## Alternatives considered

- **Fork of dbt-core's freshness code.** Rejected: it is Jinja/macro-shaped, its
  thresholds are per-source fixed durations, and a fork carries the entire dbt
  release process.
- **An Elementary test package.** Rejected: our semantics are deterministic and
  rule-based; expressing them as an Elementary test would inherit its schedule and
  its anomaly framing, and still would not run outside dbt.
- **A dbt macro only.** Rejected: no verdict contract, no report schema, no
  standalone use.
