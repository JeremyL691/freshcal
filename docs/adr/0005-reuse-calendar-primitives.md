# 0005. Reuse holidays, croniter, and zoneinfo for calendar primitives

## Status

Accepted.

## Date

2026-09-29.

## Context

FreshCal needs three primitives it must not get wrong: public and financial-market
holiday dates, cron field matching, and time-zone and DST rules. Each has a mature,
maintained implementation — `holidays` (MIT, 0.105, `py.typed`, XECB supported years
1999–2100), `croniter` (MIT, 6.2.4, maintained in the Pallets Community Ecosystem),
and the standard library's `zoneinfo` (with `tzdata` as a Windows fallback).
Reimplementing holiday tables is a data-maintenance project; reimplementing DST
arithmetic is a known source of silent bugs.

## Decision

Use `holidays` for holiday dates, `croniter` for cron matching, and `zoneinfo` for
time zones. FreshCal owns only what
composes them: the release-generation algorithm, the business-day predicate, the
DST resolution policy (ADR 0012), and the verdict. `croniter` is called in exactly one
place, always with naive datetimes, so it does pure wall-clock field matching; a
never-firing expression is converted into a configuration error (E209). `holidays`
enters only through the `CalendarProvider` port, is queried per year, and is range
checked (E405). `holidays`' `weekend_workdays` is deliberately not imported (§3.3);
make-up workdays come from explicit overrides (ADR 0006).

## Consequences

Holiday data quality is bounded by the upstream project — documented as a risk (R4)
and mitigated by overrides and by `valid_until` (E408/W005). A `croniter` behaviour
change is detectable because its use is confined to one function and pinned in
`uv.lock`, with property tests P10–P13 over generated schedules; `cronsim` is the
documented fallback if maintenance stalls (R3). The core stays free of `holidays`
(the import-linter contract forbids it), so the golden tests can run against a fake
provider, and real-data validation (§9.10 A) pins the library's actual behavior
against 7,102 ECB publication dates.

## Alternatives considered

- **Write our own holiday tables.** Rejected: unbounded data maintenance, no
  attribution story, and it is exactly the non-goal of §1.4.
- **`pandas` market calendars or `exchange_calendars`.** Rejected: a heavy dependency
  for a few closure days; `holidays` already provides `financial` calendars such as
  XECB and XNYS.
- **`pytz` for zones.** Rejected: `zoneinfo` is stdlib, and `pytz`'s
  `localize`/`normalize` API does not express "first occurrence of an ambiguous wall
  time" as directly as `fold` does.
- **Parse cron ourselves.** Rejected: day-of-month/day-of-week OR semantics, `L`, and
  `#` are fiddly; a maintained implementation with stubs exists.
