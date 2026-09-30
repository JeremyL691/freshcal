# 0011. Load-time arrival semantics; period-column mode deferred

## Status

Accepted.

## Date

2026-09-29.

## Context

"Has the data arrived?" has two meanings. *Load-time*: is the newest load timestamp at
or after the release instant? *Period-column*: does data whose business period equals
the release's period exist? Load-time matches dbt's existing freshness model and needs
one query (`max(loaded_at_field)`), but it cannot distinguish a backfill of old rows
from a fresh delivery — a Monday reload of Friday's data makes Monday's missing release
look arrived (G36) — and a late-but-present release looks on time (G35). Period-column
mode would answer the tighter question but doubles the semantic surface: each rule
needs a mapping from release to expected period (same day, previous business day,
previous month), each mapping has its own calendar edge cases, and the query shape
changes (per-period existence or `max(period_column)`).

## Decision

v0.1 uses load-time semantics: release *R* has arrived iff the observed timestamp *O*
is present and `O >= R.instant`. The product promise is narrowed
to the current-state question — "right now, is any release whose deadline has passed
still missing?" — and stated identically in §1.1, §3.7.3, the README, and
`docs/semantics.md`. ON_TIME is explicitly **not** a punctuality record. The
limitations are pinned by golden scenarios G35 and G36 so they cannot change silently,
and the mitigations available in v0.1 are `filter`, `explain`, and the arrival-time
replay that measures real false positives. Period-column mode and a punctuality audit
are the first two roadmap items (§15).

## Consequences

Adoption is cheap — users can point FreshCal at the same `loaded_at_field` dbt already
uses — and the whole algorithm fits in one pure function whose input is a single
timestamp. In exchange, users who need per-period guarantees (a reload can hide a
missing release) are not served in v0.1, and the documentation must say so wherever a
status is explained: a false green is the failure mode this design accepts. The
decision is revisited only with an ADR, which must also address the false-green risk.

## Alternatives considered

- **Period-column mode in v0.1.** Rejected: it would ship two semantic dimensions
  before the schedule and verdict engine is proven, and it requires per-source
  period-mapping configuration users cannot write correctly without the load-time
  version as a reference point.
- **Both modes in v0.1 with a per-source switch.** Rejected: two code paths through
  every edge case, and the golden scenarios would need a second dimension.
- **A punctuality history instead of a verdict.** Rejected for v0.1: it is a different
  product shape (a report over a window, not a check with an exit code) and needs
  data retention decisions.
