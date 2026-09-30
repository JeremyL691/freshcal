# 0013. Wall-clock grace windows

## Status

Accepted.

## Date

2026-09-29.

## Context

A release instant says when data *should* be available; a pipeline still needs time to
fetch, transform, and load it. Grace models that latency: the deadline is
*D = R + g*, and a missing release is only OVERDUE after *D*. The open question is
whether *g* is elapsed time or business time — "by 22:00 the next business day" would
skip weekends. Business-time grace needs its own definition (which calendar, whether
holidays count, how partial days work) and would make deadlines non-monotone in a way
that complicates the whole verdict algorithm.

## Decision

Grace is wall-clock elapsed time: a `timedelta` in
`[0, 366 days]`, added to the release instant in UTC, so a deadline that crosses a DST
change is exactly *g* elapsed hours later, and a Friday release with 6h grace has a
Saturday deadline even though Saturday is not a business day. Consequently deadlines
are strictly increasing across releases, the missed ones are a prefix of the unarrived
ones, and the verdict depends only on the first unarrived release (§3.1, §3.7).
Users who want "by the next business morning" model their own landing schedule (for
example `business_days` at 09:00) instead of a business-time grace. Business-time
grace is a roadmap item (§15 item 6).

## Consequences

The deadline arithmetic is one addition, explainable in one sentence, and monotone —
which is what makes property test P4 (monotone in grace) and P5 (monotone in time)
meaningful and the counting of missed releases correct. The limitation must be stated
where users configure grace: an overnight or weekend deadline is intentional, not a
bug, and a 22:00 business-day release with 6h grace is due on Saturday at 04:00
(G26 pins it).

## Alternatives considered

- **Business-time grace.** Rejected for v0.1: it needs a second calendar interpretation
  and makes deadlines non-monotone (a longer grace could produce an earlier deadline
  across a holiday), which would invalidate the prefix property the algorithm relies
  on.
- **Deadline = next business instant after R + g.** Rejected: same non-monotonicity,
  and it silently changes the meaning of configurations written for the simple rule.
- **No grace (deadline = release).** Rejected: every pipeline would alert immediately
  on a normal loader delay; grace is the mechanism that makes the check usable.
