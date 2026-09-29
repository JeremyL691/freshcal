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

`validation/ecb/collect.py` records two headers of the ECB's daily file whenever it runs:
`Last-Modified` (the origin server's belief about when the representation was last
modified — a *proxy* for publication time) and the `Date` of the first response that
showed that pair (an upper bound on when the file was observably available). The replay
below simulates two loaders from those proxy times, evaluates the ECB rule every 15
minutes with FreshCal's own core, and compares each verdict with a proxy reference. It is
reproduced verbatim from `uv run python validation/replay_ecb.py`; the committed data
file — not this document — is the evidence.

### Agreement with the `Last-Modified` publication proxy

Produced by `uv run python validation/replay_ecb.py` on 2026-09-29.

These results measure agreement with the Last-Modified publication proxy, not arrival accuracy.

- Collected business days: **1** (rate dates in the file: 1, span 2026-09-28 … 2026-09-28).
- Same-day revisions: none collected.
- Publication-proxy local times (Europe/Berlin): 15:56 (x1).
- `first_seen - last_modified` gap: min 1103 min, max 1103 min (1 observations).

**Insufficient data (1 business days).** Fewer than 20 collected business days cannot support any statement about arrival-time agreement, so the README makes no timing claim. The matrices below are printed for completeness only.

| Model                                          | Agree: overdue | False alarms | Missed catch-up | Agree: nothing due |
|------------------------------------------------|----------------|--------------|-----------------|--------------------|
| FreshCal, immediate loader                     | 0              | 24           | 0               | 9                  |
| FreshCal, hourly loader (first HH:05)          | 0              | 0            | 0               | 32                 |
| dbt-style fixed threshold (`error_after: 26h`) | 0              | 0            | 0               | 97                 |

*Agree: overdue* = both the model and the proxy reference report a missing release; *false alarms* = the model alerts while the proxy says the data was published; *missed catch-up* = the proxy says the data is late while the model stays quiet.

**Every disagreement, with its explanation:**

- FreshCal, immediate loader: 24 false alarm(s) — the file for 2026-09-28 was observably available before the configured release time (proxy 15:56 CEST vs release 16:00 CEST), so an immediate loader's timestamp predates the release and the release looks missing (BLUEPRINT.md §3.6). Sampled instants: 2026-09-28 18:15 CEST, 2026-09-28 18:30 CEST, 2026-09-28 18:45 CEST, 2026-09-28 19:00 CEST, 2026-09-28 19:15 CEST, 2026-09-28 19:30 CEST, 2026-09-28 19:45 CEST, 2026-09-28 20:00 CEST, 2026-09-28 20:15 CEST, 2026-09-28 20:30 CEST, 2026-09-28 20:45 CEST, 2026-09-28 21:00 CEST, 2026-09-28 21:15 CEST, 2026-09-28 21:30 CEST, 2026-09-28 21:45 CEST, 2026-09-28 22:00 CEST, 2026-09-28 22:15 CEST, 2026-09-28 22:30 CEST, 2026-09-28 22:45 CEST, 2026-09-28 23:00 CEST, 2026-09-28 23:15 CEST, 2026-09-28 23:30 CEST, 2026-09-28 23:45 CEST, 2026-09-29 00:00 CEST.

### Why the example config still uses 16:00

The one collected proxy time (15:56 CEST) is earlier than the example's configured 16:00
release time, which is exactly the early-publication effect the replay reports above. With
a single observation there is no distribution to derive a time from, so
`examples/ecb/freshcal.yml` keeps the illustrative 16:00 and this document records the
observation instead. T-6.7's criterion is conditional on the collected data: once at least
20 business days exist, the example's time is set from the observed proxy distribution and
the README gains a timing statement.
