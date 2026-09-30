# 0004. Python 3.11 or newer with mypy strict

## Status

Accepted.

## Date

2026-09-29.

## Context

FreshCal's correctness claims rest on time-zone handling, `date`/`datetime` types, and
frozen data structures; most of the bugs such a tool can have are type-level (naive
versus aware datetimes, `None` where a value is required, a `date` where an instant is
expected). The runtime features we need — `zoneinfo`, `enum.StrEnum`,
`dataclass(slots=True)`, `X | Y` annotations — are available from Python 3.11.
Supported interpreters in 2026: 3.11 to 3.14.

## Decision

The package requires Python ≥ 3.11 and is fully annotated; `mypy --strict` runs on
`src/` on every check, with `warn_unreachable` enabled (§10.2,
§10.3). Tests are not type-checked in v0.1. CI runs the test suite on 3.11, 3.12,
3.13, and 3.14, and the lint/type job runs once on the newest interpreter.

## Consequences

Type errors become failures rather than warnings, which is what makes the invariant
"all instants in the core are aware UTC" enforceable at the boundary
(`EvaluationResult.__post_init__` rejects naive values as a second line of defence).
Contributors pay the cost of annotations and occasional `cast`s for genuinely dynamic
boundaries (psycopg's `LiteralString` in ADR 0008). Python 3.10 and older are
unsupported, which excludes some long-lived environments but keeps our minimum the
oldest release still receiving security fixes during v0.1's lifetime.

## Alternatives considered

- **3.10 minimum.** Rejected: no `enum.StrEnum`, and 3.10 reaches end of life on
  2026-10-31 (Appendix A) — supporting it would mean dropping it within weeks.
- **Duplicate the time-zone handling with `pytz`.** Rejected: `zoneinfo` is stdlib and
  PEP 495 `fold` semantics are precisely what ADR 0012 needs.
- **typing without strict mypy (annotations only).** Rejected: annotations that are
  never checked do not prevent the naive/aware class of bug this project is most
  exposed to.
