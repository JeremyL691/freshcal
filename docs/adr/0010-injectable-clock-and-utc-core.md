# 0010. Injectable clock, UTC-only core, and explicit zones for naive timestamps

## Status

Accepted.

## Date

2026-09-29.

## Context

Three time zones can be involved in one evaluation — the schedule's zone, the
warehouse's stored zone, and the machine's zone — and conflating them produces results
that are wrong in a way nobody notices (a release looks on time because the check
compared local times). Two specific traps were found while writing the blueprint:
DuckDB returns `TIMESTAMPTZ` in the *machine's* zone unless told otherwise, and a naive
column silently interpreted as UTC can turn a genuine OVERDUE into ON_TIME (G20a
versus G20b). Reproducing a result also requires that "now" be an input, not a
side effect.

## Decision

The core never reads the clock or the machine's zone: `now` is a parameter supplied by
an injected `Clock`, and every instant inside `freshcal.core` is an aware UTC
`datetime` (BLUEPRINT.md §1.5 F9, §3.2). Reading the system clock is banned outside
`freshcal/adapters/clock.py` and enforced by ruff `TID251`. Naive values from the
warehouse are interpreted in `observed_timezone`, which must be configured explicitly
— there is no implicit default, not even UTC — and its absence is a configuration
error (E214, exit 2), not a warning. Sessions are forced to UTC in both adapters.
Aware values ignore `observed_timezone` with W002; a value more than five minutes in
the future adds W003.

## Consequences

Every result is reproducible from `(rule, observed value, now)`, which is what makes
golden scenarios, property tests, and the replay in T-6.7 possible; the golden suite
runs under three process time zones and must give identical answers. Users pay one line
of configuration per source with a naive column, in exchange for never getting a
silently mis-zoned verdict. The ban is checkable (T-0.2 verifies the guard fires), and
the clock adapter is the single audited place that touches wall-clock time.

## Alternatives considered

- **Assume UTC for naive values with a warning.** Rejected: warnings do not change exit
  codes, so automated callers would act on a wrong verdict; the blueprint review of
  2026-09-29 replaced exactly this behavior (W001, now retired).
- **Read `datetime.now()` where needed.** Rejected: untestable, and it makes the
  verdict depend on the machine.
- **Store local times and convert lazily.** Rejected: local arithmetic across DST
  boundaries is where the bugs are; converting once at the boundary keeps the core
  UTC-only (ADR 0012).
