# Validation inputs

Golden tests prove that the code matches the specified semantics; the data here lets the tests
prove that the *calendar model* matches the world. Only dates are
stored — never rates, prices, or any other published value.

## `ecb/publication_dates.csv`

Every day the ECB published euro foreign exchange reference rates, from 1999-01-04 to
2026-09-28 (7,102 dates, one per line, header `date`).

- Source: European Central Bank, *Euro foreign exchange reference rates*,
  `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip` (the `Date` column of
  `eurofxref-hist.csv`), retrieved 2026-09-29. The ECB permits reuse with the ECB cited
  as the source.
- Used by `tests/validation/test_calendar_history.py` (V-01): FreshCal's
  `business_days` schedule with the `financial: XECB` calendar must reproduce this set
  exactly.
- To refresh: download the zip, extract `eurofxref-hist.csv`, and write the sorted
  `Date` column (dates ≤ the span you want) as ISO dates under a `date` header. Refresh
  only when extending the span; the committed file is the evidence for the recorded
  result.

## `ust/publication_dates.csv`

Every business day the US Treasury published the daily par yield curve, 2023-01-03 to
2025-12-31 (749 dates, header `date`).

- Source: U.S. Department of the Treasury, *Daily Treasury Par Yield Curve Rates*,
  `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/<year>/all?type=daily_treasury_yield_curve&field_tdr_date_value=<year>&page&_format=csv`
  for 2023, 2024, and 2025, retrieved 2026-09-29. The `Date` column (`MM/DD/YYYY`) is
  converted to ISO dates; the rate columns are discarded.
- Used by V-02 (with `country: US`), V-03 (with `financial: XNYS`), and V-04 (with
  `ust/calendar.yml`). The three runs are the evidence that no library calendar matches
  this source and that overrides do.
- To refresh: download the three CSVs and repeat the same conversion.

## `ust/calendar.yml`

The calendar that reproduces the Treasury publication dates exactly: `country: US` plus
two extra non-working days (Good Friday 2024-03-29 and 2025-04-18, when the bond market
was closed) and one extra working day (2023-11-10, Veterans Day observed, when the bond
market was open). `valid_until: 2025-12-31` records the last date whose data was
checked.

## `ecb/publications.csv` and `ecb/collect.py`

Publication-*time* proxies (the daily file's `Last-Modified` and the first response's
`Date`), collected by `ecb/collect.py`; see `ecb/README.md`. They feed the arrival-time
replay (T-6.7, §9.10 B) and are deliberately separate from the date history above,
which is a definitive publication record.

## What these files do not prove

They cover release *dates* for two public sources, not times of day, and nothing about
a user's own pipelines. §9.10's results are the only real-world accuracy statements
FreshCal makes, and the README recommends running FreshCal in shadow mode next to
existing checks before relying on it for a private source.
