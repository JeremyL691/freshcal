# 0014. Cron non-business-day default none; clamping for Nth business day

## Status

Accepted.

## Date

2026-09-29.

## Context

Two schedule kinds have an "undefined day" problem. A `cron` expression such as
`0 6 * * *` fires on days the calendar calls non-business days; silently applying a
holiday calendar would change the meaning of a plain expression and drop weekend
releases that the user may genuinely want. A `monthly_business_day` rule such as "the
22nd business day" can be unsatisfiable — February 2026 has only 20 business days —
and skipping the month would hide a missed monthly release, which is the less
conservative failure.

## Decision

For `cron`, the default policy is `none`: the expression is authoritative and the
calendar is not consulted; users opt in to `skip`, `following`, or `preceding`
(BLUEPRINT.md §3.4.3, §3.8.1). A roll keeps the local time of day, records
`adjusted_from` (the original date), and fails with E407 if no business day is found
within 31 days. For `monthly_business_day`, `business_day: N` selects the Nth business
day and `-N` counts from the end; if the month has fewer than |N| business days the
release is **clamped** to the last (N > 0) or first (N < 0) business day and
`Release.clamped` is set; a month with no business day produces no release. `0` and
|N| > 23 are rejected (E204, since no month has more than 23 weekdays), and
`on_non_business_day` on a business-day schedule is rejected (E205).

## Consequences

Plain cron keeps plain cron's meaning, and every calendar interaction is an explicit
line in the config that `explain` can show (`adjusted from 2026-04-06`). Monthly
publishers keep a release every month, so a missing month is reported rather than
disappearing; the clamped flag makes the deviation visible in the JSON report. The
cost is that a user who expects cron to respect holidays must set `policy` explicitly —
documented, and the reason `none` is stated in the field reference with its rationale.
Golden scenarios G13, G14, G31, G11, G12 and unit tests U-SCH-02–07, U-CAL-06/U-CAL-07
pin the behavior; the roll bound is tested by U-CAL-07.

## Alternatives considered

- **Cron defaults to `following` (holiday-aware).** Rejected: it would silently change
  the meaning of existing expressions and contradict cron's own day-of-week field.
- **Skip a month with too few business days.** Rejected: the monthly obligation would
  vanish from the report exactly when the pipeline is most broken.
- **Reject out-of-range `business_day` at evaluation time instead of load time.**
  Rejected: E204 at load is earlier and cheaper.
- **Clamp silently.** Rejected: the flag costs one field and lets an operator see that
  the release they are reading is not the one they configured.
