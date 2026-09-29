# Validation

FreshCal's golden tests prove that the code matches `BLUEPRINT.md`. This document reports
the two checks that go outside the blueprint and compare FreshCal with real public data
(BLUEPRINT.md §9.10). Only results that were actually produced are reported here; where a
number is missing, the text says so instead of estimating.

Two kinds of evidence are kept apart on purpose:

- **Release dates** against complete publication histories (section A) — a definitive
  record of *which days* a publisher published.
- **Arrival times** against the `Last-Modified` header of the ECB's daily file
  (section B) — a *proxy* for when the file became public, never proof of it.

## Section A — Release dates against publication history

Produced by `uv run pytest tests/validation -q` on 2026-09-29 (Python 3.11.15,
`holidays` 0.105). The test uses FreshCal's own `releases_between` with a
`business_days` schedule and the real `HolidaysCalendarProvider`; the committed inputs
are described in `validation/README.md`. "Expected but not published" would be a false
OVERDUE for that day; "published but not expected" would be a day FreshCal never checks.

### ECB euro reference rates, 1999-01-04 … 2026-09-28 (7,102 dates)

| FreshCal rule | Matched | Expected but not published | Published but not expected |
|---|---|---|---|
| `business_days`, weekend Sat/Sun, `financial: XECB` | 7,102 | 0 | 0 |

The TARGET calendar reproduces every day the ECB published a reference rate since 1999,
including the Easter and Christmas closures and the days the ECB published although the
US market was closed.

### US Treasury daily par yield curve, 2023-01-03 … 2025-12-31 (749 dates)

| FreshCal rule | Matched | Expected but not published | Published but not expected |
|---|---|---|---|
| `business_days`, `country: US` | 748 | 2 | 1 |
| `business_days`, `financial: XNYS` | 747 | 5 | 2 |
| `business_days`, `country: US` + overrides (`validation/ust/calendar.yml`) | 749 | 0 | 0 |

The disagreements, all explained:

- **`country: US`, expected but not published — 2024-03-29 and 2025-04-18 (Good
  Friday).** The federal holiday calendar does not contain Good Friday; the bond market
  closes on it, so no yield curve was published. A federal-calendar rule would raise a
  false OVERDUE on both days.
- **`country: US`, published but not expected — 2023-11-10.** Veterans Day fell on a
  Saturday in 2023 and was observed on Friday 2023-11-10, a federal holiday; the bond
  market stayed open and published a curve. A federal-calendar rule would never check
  that day, so a missing publication there would go unnoticed.
- **`financial: XNYS`, expected but not published — 2023-10-09, 2024-10-14, 2024-11-11,
  2025-10-13, 2025-11-11 (Columbus Day and Veterans Day).** The stock exchange closes on
  those days, but the bond market trades and the Treasury publishes; five false alarms.
- **`financial: XNYS`, published but not expected — 2023-04-07 and 2025-01-09.** The
  exchange closed on Good Friday 2023 and on the national day of mourning for Jimmy
  Carter on 2025-01-09, while the Treasury published both days: two days FreshCal would
  never check.
- **With overrides, no disagreement remains.** Two extra non-working days (Good Friday
  2024-03-29, 2025-04-18) and one extra working day (2023-11-10) turn the federal
  calendar into one that matches all 749 publications. This is the concrete evidence for
  the calendar-override feature (ADR 0006) and for `valid_until` discipline: the
  overrides are year-specific, and the calendar records the last date a person checked.

**Scope.** These checks cover release *dates* only, not times of day, and only these two
sources. They say nothing about a user's own pipelines.

## Section B — Arrival times against the ECB publication proxy

Not yet run. `validation/ecb/collect.py` (T-0.5) has recorded one publication-time proxy
row so far (rate date 2026-09-28, `Last-Modified` 2026-09-28T13:56:44Z, first seen
2026-09-29T08:19:25Z), and the replay needs at least 20 collected business days before
any statement is made (T-6.7). Until then the README makes no timing claim.

When it runs, this section will report agreement with the `Last-Modified` publication
proxy, list every disagreement with its explanation, and state that the results measure
agreement with a proxy rather than arrival accuracy.
