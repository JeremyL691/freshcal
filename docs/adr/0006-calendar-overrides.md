# 0006. Calendar overrides and fully custom calendars

## Status

Accepted.

## Date

2026-09-29.

## Context

Library holiday data is never complete for the days that matter most operationally:
make-up workdays announced year by year (China), one-off company closures, exchange
calendars that differ from the national holiday list, and sources whose publisher
follows a market calendar rather than a national one. Real-data validation shows the
need concretely: the US Treasury publishes on days that `country: US` marks as
holidays and skips Good Friday, which the federal calendar does not
(BLUEPRINT.md §9.10 A). A tool that can only say "use a different country code" fails
those users.

## Decision

Every rule's calendar is a weekend set, a list of holiday-calendar references, and a
set of overrides: **extra working days** (`working_days`) and **extra non-working
days** (`non_working_days`), inline or from an override file, with union semantics
across entries (BLUEPRINT.md §1.5 F5, §3.3, §4.3, §4.5). Precedence is fixed: an
explicit working day wins over everything, an explicit non-working day wins over
weekend and library holidays, and a date in both is an error (E403) caught at load
time. A calendar with overrides should declare `valid_until` — the last date whose
data a human checked — because override dates are usually specific to one year;
overrides without it produce warning W006, and evaluating past it fails with E408.
FreshCal still ships no holiday data of its own; overrides are the supported way to
correct it.

## Consequences

Users can express the Treasury case, Chinese make-up Saturdays (G17), and custom
company calendars without waiting for an upstream release. Correctness now depends on
someone re-checking the dates yearly, so the expiry mechanism (E408) fails loudly
rather than silently drifting, and W006/W005 give advance notice. Test coverage is
explicit: G17/G17b (make-up workday present and absent), U-CAL-01–10, and the
`validation/ust/calendar.yml` case that converts two false alarms and one unchecked
day into zero mismatches.

## Alternatives considered

- **Only `holidays` calendars, no overrides.** Rejected: validation showed it cannot
  reproduce the Treasury publication set, and it would force users to patch the
  library or run a patched fork.
- **Import `holidays`' `weekend_workdays` to model make-up workdays.** Rejected: union
  semantics across several calendars are ill-defined for make-up days (a Chinese
  make-up Saturday must not make Saturday a TARGET working day), and one explicit
  source of truth for working-day exceptions is easier to review (§3.3).
- **Allow arbitrary per-date patches of library data.** Rejected: two mechanisms for
  the same job; overrides are simpler and visible in the config.
