# FreshCal

Business-calendar-aware data freshness checks: declare *when* data should arrive (cron, business days, Nth or last business day of the month, holiday calendars, overrides, time zone) and *how late* it may be (a grace window); FreshCal tells you whether a due release is currently missing.

Status: under development. See [BLUEPRINT.md](BLUEPRINT.md).

## Development

```bash
uv sync --all-extras
uv run pytest
```
