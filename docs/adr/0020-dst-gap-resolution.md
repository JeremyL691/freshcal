# 0020. DST-gap resolution: the transition instant instead of the gap length

## Status

Deferred (2026-09-30). FreshCal 0.1.2 ships the current behaviour (a local time in a spring-forward gap is shifted forward by the gap length); this alternative can be revisited if users need cron(8)-style resolution.

## Date

2026-09-29.

## Context

When a schedule's local wall time does not exist because the clock springs forward, FreshCal
resolves it with PEP 495 `fold=0` and the stdlib's shift-forward rule: Europe/Berlin
`30 2 * * *` on 2026-03-29 becomes 03:30 CEST (01:30Z), classified `gap`
(ADR 0012). The shift moves the release forward by the
full length of the gap even though the transition itself — 03:00 CEST (01:00Z) — is when
the wall clock passes the scheduled time.

An independent black-box review of v0.1.0 hand-worked spring-gap cases for Berlin, New York and
Australia/Lord_Howe (30-minute gap) and observed that every remaining mismatch is this
DST-gap class: the review's expected answers follow what cron implementations do, and
cron(8) documents the behaviour: for a jump under three hours, "those jobs that would have
run in the interval that has been skipped will be run immediately"
(man7.org/linux/man-pages/man8/cron.8.html, checked 2026-09-29; the same text underlies
cronie and the Vixie cron it derives from). A pipeline whose scheduler follows that
convention sees the 02:30 job run at the 03:00 transition, so its data can exist at
01:00Z while FreshCal does not consider the release due until 01:30Z.

The stakes are the same as in ADR 0012: a release instant that is too late delays an
alert (a genuine failure stays invisible for the gap remainder until the deadline
passes), while one that is too early can raise a false alarm for a publisher that fires at
the shifted wall time (03:30).

## Decision

We propose to resolve a local time that falls in a spring-forward gap to the **UTC
transition instant** — the moment the zone's offset changes — instead of adding the gap
length to the wall time. Berlin 2026-03-29 02:30 would then be 01:00Z (03:00 CEST), and
Lord Howe's 30-minute gap would resolve 02:10 to 02:30 rather than 02:40 local. The
classification stays `gap`; the fall-back overlap keeps `fold=0` and its first occurrence
(ADR 0012), and every other §3.2 rule is untouched.

This record is `Deferred`: the code keeps shifting by the gap length. If adopted, the
change is an algorithm change of its own: it updates
§3.2/§3.8.3, `resolve_local`/`classify_local`, the golden rows that pin gap instants
(G15, G42–G44), the
unit and property tests, `docs/semantics.md` and the `explain` annotation wording.

## Consequences

Positive: the release instant matches what cron-driven pipelines actually experience, so a
failure at the transition is detected as soon as the scheduler would have run a second
time, and the "expect data earlier, never later" rationale of §3.2.3 holds without the
qualification that a gap can delay the expectation. Negative: a publisher that really
fires at the shifted wall time (03:30) is judged earlier — up to one gap length (one hour
in Berlin, 30 minutes on Lord Howe), producing a possible false alarm of that duration;
and every gap test and golden row changes at once, which is exactly why this stays a
proposal until the owner decides. The drift guard is the oracle suite (`tests/oracle/`,
rule P1): whichever rule is chosen must be implemented in one place and kept green.

## Alternatives considered

- **Keep the shift-by-gap-length rule (status quo).** Rejected by the
  proposal for the reason above; it remains the implemented behaviour until the owner
  decides, and it is defensible for schedules whose provider follows a wall-clock
  scheduler that also skips the interval.
- **Skip the release entirely.** Rejected in ADR 0012 and again here: the provider may
  still publish, and a skipped day hides a miss.
- **Resolve to the first instant after the gap with the original time of day**
  (02:30 → 03:00 + 30 = 03:30 is the current rule; "first valid wall time at or after the
  nominal" is the same thing). It is the current behaviour under another name and fails
  the cron comparison the same way.
- **Fire twice around the transition (once at the transition, once at the shifted wall
  time).** Rejected: duplicates one delivery obligation, and the load-time model cannot
  tell two same-instant deliveries apart (§3.4.2, ADR 0011).
