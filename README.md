# FreshCal

Business-calendar-aware data freshness checks: declare *when* data should arrive (cron, business days, Nth or last business day of the month, holiday calendars, overrides, time zone) and *how late* it may be (a grace window); FreshCal tells you whether a due release is currently missing.

Status: under development. See [BLUEPRINT.md](BLUEPRINT.md).

## 30-second quickstart

FreshCal v0.1 is not on PyPI; run it from a clone:

```bash
git clone <repository-url> && cd freshcal
uv sync --extra duckdb
```

The example config reads a small CSV with DuckDB, applies the ECB's schedule (business
days at 16:00 Europe/Berlin, TARGET holidays, 2 h grace) and is evaluated three times on
Monday 2026-09-28. `examples/ecb/fx_rates.csv` is **synthetic** sample data shaped like
ECB reference rates, not the real rates.

<!-- quickstart:command:1 -->
```bash
uv run freshcal check -c examples/ecb/freshcal.yml --now 2026-09-28T07:30:00+02:00
```
<!-- quickstart:output:1 -->
```text
FreshCal 0.1.0 | evaluated at 2026-09-28T05:30:00Z | 1 source

SOURCE        STATUS   RELEASE                    DEADLINE                   OBSERVED                   NEXT EXPECTED
ecb.fx_rates  ON_TIME  Fri 2026-09-25 16:00 CEST  Fri 2026-09-25 18:00 CEST  Fri 2026-09-25 16:07 CEST  Mon 2026-09-28 16:00 CEST

ecb.fx_rates: On time: latest release Fri 2026-09-25 16:00 CEST arrived (observed Fri 2026-09-25 16:07 CEST); next release Mon 2026-09-28 16:00 CEST.

Summary: 1 ON_TIME | exit code 0
```

Monday morning with Friday's data is **not** an alert.

<!-- quickstart:command:2 -->
```bash
uv run freshcal check -c examples/ecb/freshcal.yml --now 2026-09-28T17:00:00+02:00
```
<!-- quickstart:output:2 -->
```text
FreshCal 0.1.0 | evaluated at 2026-09-28T15:00:00Z | 1 source

SOURCE        STATUS   RELEASE                    DEADLINE                   OBSERVED                   NEXT EXPECTED
ecb.fx_rates  NOT_DUE  Mon 2026-09-28 16:00 CEST  Mon 2026-09-28 18:00 CEST  Fri 2026-09-25 16:07 CEST  Tue 2026-09-29 16:00 CEST

ecb.fx_rates: Not due: release Mon 2026-09-28 16:00 CEST has not arrived yet; grace window ends Mon 2026-09-28 18:00 CEST; next release Tue 2026-09-29 16:00 CEST.

Summary: 1 NOT_DUE | exit code 0
```

At 17:00 the release is still inside its grace window, so nothing is wrong yet.

<!-- quickstart:command:3 -->
```bash
uv run freshcal check -c examples/ecb/freshcal.yml --now 2026-09-28T18:30:00+02:00
```
<!-- quickstart:output:3 -->
```text
FreshCal 0.1.0 | evaluated at 2026-09-28T16:30:00Z | 1 source

SOURCE        STATUS   RELEASE                    DEADLINE                   OBSERVED                   NEXT EXPECTED
ecb.fx_rates  OVERDUE  Mon 2026-09-28 16:00 CEST  Mon 2026-09-28 18:00 CEST  Fri 2026-09-25 16:07 CEST  Tue 2026-09-29 16:00 CEST

ecb.fx_rates: Overdue: release Mon 2026-09-28 16:00 CEST missed its deadline Mon 2026-09-28 18:00 CEST; 1 release missed; latest data observed Fri 2026-09-25 16:07 CEST.

Summary: 1 OVERDUE | exit code 1
```

Half an hour later the deadline has passed and the run exits 1, so a scheduler or CI job
can act on it.

## Development

```bash
uv sync --all-extras
uv run pytest
```
