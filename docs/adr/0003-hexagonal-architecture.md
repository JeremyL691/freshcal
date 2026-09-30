# 0003. Hexagonal architecture with a pure core

## Status

Accepted.

## Date

2026-09-29.

## Context

FreshCal's value is a rule engine: given a schedule, a calendar, a grace window, and
one observed timestamp, decide ON_TIME, NOT_DUE, or OVERDUE. That
computation must be testable exhaustively and reproducibly — golden scenarios,
property tests, and real-publication-history checks all depend on it. Everything
around it (YAML parsing, the `holidays` library, DuckDB/PostgreSQL, JSON output) is a
replaceable detail with its own failure modes and its own clock.

## Decision

The package is layered around a pure core:
`freshcal.core` computes verdicts and imports only the standard library and
`croniter`; `freshcal.app` orchestrates use cases and imports only the core;
`freshcal.config` and `freshcal.adapters` translate outside formats and services;
`freshcal.cli` is the composition root that wires everything. Dependencies point
inward only, and the rule is machine-enforced by three import-linter contracts
(`uv run lint-imports`), including external packages, plus ruff `TID251` for the clock
ban.

## Consequences

The core is testable without a database, a network, or a real clock; golden tests run
in three process time zones and must give identical results (§3.8.14). Adapter
failures cannot corrupt semantics, and a new warehouse is an additive adapter. The cost is a port per outside interaction (§5.4), some
boilerplate in `cli.py`, and a test suite that must keep the contracts honest
(the T-0.2 probes prove both guards actually fire).

## Alternatives considered

- **Flat module layout with direct imports.** Rejected: it makes "the engine is pure"
  an aspiration rather than a checkable invariant, and the first convenience import
  of `holidays` into verdict logic would be invisible.
- **Layers by feature instead of by purity.** Rejected: the schedule/verdict engine is
  the one part where correctness claims are made; it must be isolatable.
- **Enforce purity by convention only.** Rejected: the guard costs one config block
  and catches the exact mistake (an adapter type leaking into the core) that would
  invalidate the golden tests' meaning.
