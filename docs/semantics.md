# Semantics

This document explains what FreshCal computes and why, in the terms it uses everywhere
else: the JSON report, the terminal output, `freshcal explain`, and the golden tests. It
is the user-facing companion to BLUEPRINT.md §3, which is normative.

## The question FreshCal answers

**Right now, is any release whose deadline has passed still missing, judged by load
timestamps?**

It is a *current-state* check, like dbt source freshness, not an audit of past
punctuality. `ON_TIME` means "nothing due is currently outstanding". It does **not**
certify that each past release arrived before its deadline, and because arrival is
inferred from load timestamps, a reload of old rows can make a missing release look
arrived. See [Limitations](#limitations).

## Definitions

| Term | Meaning |
|---|---|
| **Release** *R* | The UTC instant at which data is expected to be available: the schedule's nominal local time, adjusted for non-business days and resolved through DST. The earliest instant at which the data may be loaded. |
| **Grace** *g* | A non-negative wall-clock duration after *R* during which data may still arrive. |
| **Deadline** *D* | *R + g*, elapsed time in UTC. The last instant at which a missing release is still acceptable. |
| **Observed** *O* | `max(loaded_at_field)` from the warehouse, converted to aware UTC. May be absent (`NULL`). |
| **Arrived** | *O* is present and *O ≥ R*. Inclusive: `O == R` counts. |
| **Occurred** | *R ≤ now*. Inclusive: `now == R` counts. |
| **Missed** | Occurred, not arrived, and `now > D`. |
| **Pending** | Occurred, not arrived, and `now ≤ D`. |

Three consequences of these definitions drive the whole implementation:

1. **Arrival is monotone.** If *O ≥ Rᵢ* then *O ≥ Rⱼ* for every earlier release, so the
   arrived releases are always a prefix of the release sequence.
2. **Deadlines are strictly increasing** because grace is constant, so the missed releases
   are a prefix of the unarrived ones and the pending ones are a suffix.
3. Therefore **the verdict depends on one release only**: the first release *U* with
   *U > O*. If *U > now* → `ON_TIME`; if *U ≤ now ≤ D(U)* → `NOT_DUE`; if *now > D(U)* →
   `OVERDUE`.

## Releases

A **nominal release** is a naive local wall-clock time produced by the schedule:

- `cron`: the next firing of a five-field expression, matched on wall-clock fields only.
  The expression is authoritative by default; `skip`, `following`, and `preceding` opt in
  to moving a release off a non-business day, keeping the time of day.
- `business_days`: the configured time on every business day of the calendar.
- `monthly_business_day`: the configured time on the Nth business day of the month, or the
  Nth from the end (`-1` is the last business day). If a month has fewer business days the
  release clamps to the last (or first) one and is flagged `clamped`; a month with no
  business day at all produces no release.

Each nominal release is then resolved to UTC with **`fold=0`**:

- a time that does not exist (spring forward) is shifted forward by the length of the gap:
  `30 2 * * *` in Europe/Berlin on 2026-03-29 → 03:30 CEST (01:30Z), classified `gap`;
- a time that occurs twice (fall back) keeps its **first** occurrence: 2026-10-25 02:30 →
  02:30 CEST (00:30Z), classified `ambiguous`.

Rationale: both choices assume the publisher still published as early as possible. At
worst that costs a false alarm as long as the transition; the alternative — expecting data
later — can hide a miss.

**One instant is one delivery obligation.** When two nominal releases resolve to the same
instant (a rolled weekend collapsing onto Monday, a gap-shifted time landing on a real
scheduled time), the instant is reported once. One load at or after that instant satisfies
every release at it, so FreshCal could not tell two same-instant deliveries apart; a
provider that really ships separate files still gets one obligation reported.

## Calendar validity and the W005 notice window

A calendar may declare `valid_until`, the last local date whose holidays and overrides a
person has checked. Evaluating after it fails with `E408`. Before that, FreshCal warns with
`W005` when the calendar is *consulted* for a date after `valid_until`, because the verdict
then rests on data nobody has verified.

The notice window is explicit, not a side effect of how far a search happens to look: for
every rule that consults its calendar — a plain `cron` with `on_non_business_day: none`
never does — the local dates in the schedule time zone that releases up to
`now + CHUNK + DATE_PADDING_DAYS` (34 days) can depend on are declared as consulted, and
every predicate or reason answer records its date, including weekend and override
decisions that never reach the provider. `check`, `next` and `validate` all compute the
warning through the same function, so they agree on the same evaluation instant, and `W005`
is never attached to a `CONFIG_ERROR` result.

## The decision table

Rows are evaluated top to bottom; the first match wins.

| # | Rule valid and evaluable? | Query succeeded? | Observed *O* | First unarrived *U* | *now > D(U)* | Status | Reported release |
|---|---|---|---|---|---|---|---|
| 1 | no | – | – | – | – | `CONFIG_ERROR` | null |
| 2 | yes | no | – | – | – | `QUERY_ERROR` | latest *R ≤ now* |
| 3 | yes | yes | NULL and no `active_from` | – | – | `NO_DATA` | latest *R ≤ now* |
| 4 | yes | yes | present (or NULL with `active_from`) | none with *U ≤ now* | – | `ON_TIME` | latest *R ≤ now* |
| 5 | yes | yes | as row 4 | *U ≤ now* | no | `NOT_DUE` | *U* (oldest pending) |
| 6 | yes | yes | as row 4 | *U ≤ now* | yes | `OVERDUE` | *U* (oldest missed) |

`ON_TIME`, `NOT_DUE`, and `OVERDUE` are verdicts about the data. `NO_DATA`,
`CONFIG_ERROR`, and `QUERY_ERROR` say the check could not produce a verdict — a
misconfiguration, an unreachable warehouse, or a missing `active_from` are never reported
as "on time".

**Reported details.** `release` is the release the status is about and `deadline` is its
deadline; `missed_count` and `pending_count` say how many unarrived releases lie on either
side of their deadlines; the next expected arrival is always strictly after `now`.

## Searching, and why the answer is always bounded

Every single search for a release spans at most **1830 days** (five 366-day years), and
counting stops after **10 000** releases, so evaluation time is bounded for any input.

A naive implementation would search backwards from `now` by a bounded amount, which can
report `ON_TIME` after looking at an interval that does not reach the observed timestamp.
FreshCal instead guarantees:

- if the observed timestamp is within the horizon, one complete search covers the whole
  interval *(O, now]* — so "no release" really means "no release";
- if the observed timestamp is older than the horizon, the search first looks for a missed
  release inside the recent 1830 days, which proves `OVERDUE` without touching ancient
  calendar data; **only when that finds no missed release** does it search forward from
  the observed timestamp itself for up to 1830 days, which finds the exact oldest
  unarrived release;
- if even that finds nothing — a gap longer than 1830 days after the observed timestamp —
  the result is `CONFIG_ERROR` `E215`, "cannot decide", never `ON_TIME`.

`missed_truncated` is true when the stale shortcut above answered (the count started from
the recent window rather than at the oldest unarrived release) and when counting hit the
10 000 cap: the counts are then lower bounds ("at least N releases missed", singular for
`N = 1`) and `release` is the oldest miss *found*.
The status never depends on the truncated part.

## Edge cases, with the ruling and the reason

| Situation | Ruling | Why |
|---|---|---|
| A release is due on a holiday or weekend | `business_days` and `monthly_business_day` simply have no release then; `cron` does nothing by default, and can `skip`, roll `following`, or roll `preceding` | Cron already has a day-of-week field; silently applying a calendar would change the meaning of a plain expression. |
| A roll cannot find a business day within 31 days | `E407` | A schedule that far from reality is a configuration problem. |
| A month has fewer business days than requested | Clamp to the last (or first) business day, flagged | A monthly publisher still publishes that month; skipping would hide a missed release. |
| DST spring forward / fall back | Shift forward by the gap / keep the first occurrence | Expect data earlier rather than later (see above). |
| `max(loaded_at_field)` is a naive timestamp | Interpret it in `observed_timezone`; without one, `E214` | Silently reading a Berlin column as UTC turns a real `OVERDUE` into `ON_TIME`, and automated callers act on exit codes. |
| `max(loaded_at_field)` is a `TIMESTAMPTZ` | Convert to UTC; `observed_timezone` is ignored with `W002` | The value carries its own zone. |
| The observed timestamp is in the future | Compute the verdict with it; warn with `W003` beyond five minutes | Most often a zone misconfiguration; the damage is a delayed alert, not a hidden one. |
| `O == R`, `now == R`, `now == D` | Arrived; occurred (pending); still inside grace | The grace window is the closed interval *[R, D]*. |
| Several releases are missed | Report the oldest miss plus the counts | "Since when" and "how bad" are different questions. |
| Grace crosses a weekend or holiday | The deadline is still *R + g* elapsed | Grace models pipeline latency, which does not pause; the calendar already governs when releases happen. |
| The table is empty and no `active_from` is set | `NO_DATA` | FreshCal cannot know which releases were expected. With `active_from`, releases exist from that date and an empty table is `ON_TIME` before the first one, then `NOT_DUE`, then `OVERDUE`. |
| A calendar has overrides and no `valid_until` | Warning `W006` at load time | Override dates are usually valid for one year. |
| Evaluation happens after `valid_until` | `E408` | Failing loudly once a year beats silently wrong verdicts for a year. Consulting later dates for the next releases warns (`W005`) instead; see "Calendar validity and the W005 notice window". |
| Several holiday calendars are listed | Union: a date is a holiday if any calendar contains it | A source that needs both London and TARGET open needs both calendars. |
| A month-day/leap-day-only schedule | Works; leap days and year boundaries need no special handling | Generation iterates real dates. |

## Explanations

Every result carries one sentence, built from the same facts as the table columns:

- `On time: latest release Fri 2026-09-25 16:00 CEST arrived (observed Fri 2026-09-25 16:07 CEST); next release Mon 2026-09-28 16:00 CEST.`
- `Not due: release Mon 2026-09-28 16:00 CEST has not arrived yet; grace window ends Mon 2026-09-28 18:00 CEST; next release Tue 2026-09-29 16:00 CEST.`
- `Overdue: release Mon 2026-09-28 16:00 CEST missed its deadline Mon 2026-09-28 18:00 CEST; 1 release missed; latest data observed Fri 2026-09-25 16:07 CEST.`
- `No data: max(_loaded_at) returned NULL; latest release Fri 2026-09-25 16:00 CEST; next release Mon 2026-09-28 16:00 CEST.`
- `Configuration error: E214 max(_loaded_at) returned a timestamp without time zone …`
- `Query error: E502 query failed: …`

The sentence is part of the JSON report and of the terminal output, so both views always
agree about what a status means. `freshcal explain` additionally prints the releases
around the evaluation instant, the local dates that had no release and why, and the three
comparisons that produced the status.

## Limitations

- **Load-time semantics.** Anything that makes `max(loaded_at_field)` recent satisfies the
  check, including a backfill of old rows, and a late arrival that is present now is
  `ON_TIME`. `filter` restricts what counts as a delivery; `freshcal explain` shows exactly
  which comparison was made. Per-period checks are a roadmap item.
- **An early publication never counts for its release.** Arrival is `O ≥ R`, so data loaded
  before the release time does not satisfy it: a schedule time later than the real earliest
  publication reports the release missing — `OVERDUE` after the deadline — until a load at
  or after `R`. On 2026-09-28 the ECB's daily file was observably available at 15:56:44
  CEST (the `Last-Modified` proxy, not proof of publication; `docs/validation.md` section
  B). Declare the earliest time your loader can see the data and widen the grace window if
  needed (BLUEPRINT.md §3.6).
- **Wall-clock grace.** A Friday 22:00 release with 6 h grace is due Saturday 04:00.
  Business-time grace is a roadmap item.
- **One obligation per instant.** Releases that roll onto the same instant count once.
- **One release per wall time in a DST overlap.** An hourly cron does not fire twice
  during a fall-back hour.
- **Holiday data** comes from the `holidays` library and your overrides; announced-yearly
  calendars need `valid_until` discipline.
- **Naive timestamps** need `observed_timezone`.
- **DuckDB and PostgreSQL only**, and DuckDB has no statement timeout in v0.1.

## Why per-period mode is deferred

Load-time semantics match dbt's existing freshness model, so FreshCal can be adopted
without changing your data. Per-period checks ("did the data *for* 2026-09-28 arrive?")
need a second rule dimension mapping each release to an expected business period, each
mapping with its own calendar edge cases, and a different query shape. Shipping both would
double the semantic surface before the schedule engine is proven, so period-column
freshness and punctuality audits are the first two roadmap items in
[../BLUEPRINT.md](../BLUEPRINT.md) §15.
