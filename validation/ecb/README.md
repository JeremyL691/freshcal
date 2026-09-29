# ECB publication-time collection

## What this is

`collect.py` appends one row to `publications.csv` for every new
`(rate_date, Last-Modified)` pair it observes on the ECB's daily euro foreign
exchange reference rate file:

```
rate_date,last_modified_utc,first_seen_utc
```

| Column | Source | Meaning |
|---|---|---|
| `rate_date` | the `time` attribute of the dated `Cube` in `eurofxref-daily.xml` | the business day the rates refer to |
| `last_modified_utc` | the `Last-Modified` response header, converted to UTC | when the origin server believes the representation was last modified |
| `first_seen_utc` | the `Date` response header of the first response that showed this pair, converted to UTC | an upper bound for when the file was observably available |

Source: European Central Bank, *Euro foreign exchange reference rates*,
<https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml>. The ECB permits reuse
of this data with the ECB cited as the source; only the two header timestamps and the
rate date are stored, never the rates themselves.

## What it does and does not prove

`Last-Modified` is a **proxy** for publication time. RFC 9110 defines it as the date
and time at which the origin server believes the representation was last modified; it
is not proof that the file was publicly available at that moment, and a corrected file
gets a new value (which is why a revision is stored as a second row for the same
`rate_date`).

`first_seen_utc` bounds availability from above, not exactly: its precision depends on
how often this script runs, and the site's `cache-control: max-age=300` can delay an
observation by up to five minutes.

Consequently the replay in `validation/replay_ecb.py` (T-6.7) reports **agreement with
the `Last-Modified` publication proxy**, never arrival accuracy, and it explains every
disagreement it finds. Days on which nothing ran are missing, not guessed.

## Running it

```bash
uv run python validation/ecb/collect.py
```

One HTTP request; exit code 0 on success or when nothing is new, 1 on a network or
parse failure (in which case the file is left untouched). The agent runs it at the
start of every session and before each task; it needs no arguments and reads no local
clock — the recorded times come only from the server's headers.

### If TLS verification fails

On macOS the uv-managed CPython has no default CA store, so the first run can fail
with `URLError: CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`.
Point OpenSSL at an existing bundle — never disable verification:

```bash
SSL_CERT_FILE=/opt/homebrew/etc/openssl@3/cert.pem uv run python validation/ecb/collect.py
```

`/etc/ssl/cert.pem` (the LibreSSL bundle) was not sufficient on the machine used for
development on 2026-09-29; the Homebrew OpenSSL bundle worked, and `curl` (which uses
its own bundle) worked throughout.

## Scheduling it

Continuous collection increases the data available to T-6.7, so a schedule is useful
but optional; nothing in the project requires it. Any of these work, and the agent does
not install any of them:

- **cron** (twice a day, at 16:30 and 18:30 UTC, weekdays):

  ```
  30 16,18 * * 1-5 cd /path/to/freshcal && /path/to/uv run python validation/ecb/collect.py
  ```

- **launchd**: a `StartCalendarInterval` job wrapping the same command, on weekdays at
  16:30 and 18:30 local time.
- **GitHub Actions**: a workflow with a `schedule:` trigger that runs the script and
  commits `validation/ecb/publications.csv` back to the repository (requires a
  remote and write permission; not configured in v0.1).

Special dates worth capturing: the DST transitions (Europe/Berlin 2026-10-25 and
2027-03-28), TARGET closing days (2026-12-25, 2026-12-26, 2027-01-01), and the
year-boundary days (2026-12-31, 2027-01-04).
