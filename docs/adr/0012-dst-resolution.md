# 0012. DST resolution with fold=0

## Status

Accepted.

## Date

2026-09-29.

## Context

A wall-clock schedule and a real instant differ twice a year. When the clock springs
forward, a scheduled local time may not exist (`30 2 * * *` in Europe/Berlin on
2026-03-29); when it falls back, one local time occurs twice (2026-10-25). Left
undefined, this produces either a missing release (a false OVERDUE or a silently
skipped day) or two releases for one wall time. Both cases are reachable with ordinary
configurations, and a hand-rolled rule ("add an hour when the offset changes") is
exactly the kind of arithmetic that quietly breaks for half-hour and 45-minute
offsets.

## Decision

Local wall times are converted to UTC by one function, `resolve_local`, which applies
PEP 495 `fold=0` (BLUEPRINT.md §3.2, §3.8.3). Consequences, all normative: a
non-existent time is shifted forward by the length of the gap (2026-03-29 02:30
Berlin → 03:30 CEST = 01:30Z, classified `gap`); an ambiguous time resolves to its
**first** occurrence (2026-10-25 02:30 → 02:30 CEST = 00:30Z, classified
`ambiguous`). `classify_local` reports which case applied, `Release.dst` carries it
into the output, and `explain` annotates it. Grace is added to UTC instants, never to
local times.

## Consequences

The behavior is stdlib-defined, so no custom DST arithmetic exists to get wrong, and
both interpretations are conservative — the earliest possible instant is expected, at
worst producing a false alarm lasting the length of the overlap, never a hidden miss.
Two documented limits follow: during a fall-back overlap each wall time yields one
release (so an hourly cron does not fire twice), and a gap-shifted release can collide
with a real one, in which case the instant is kept once (one instant, one delivery
obligation). Golden scenarios G15/G16, unit tests U-TIME-02–04 and U-SCH-11, and
property test P14 pin the behavior.

## Alternatives considered

- **Skip releases whose local time does not exist.** Rejected: a provider whose
  scheduler fires at the shifted wall time would then be reported late; shifting
  forward assumes the provider still publishes.
- **Resolve ambiguous times to the second occurrence (fold=1).** Rejected: expecting
  data later hides a miss for up to an hour, which is the failure we most want to
  avoid.
- **Generate both occurrences.** Rejected: duplicates one delivery obligation, and the
  load-time model cannot tell the two apart (ADR 0011).
- **Custom offset arithmetic.** Rejected: half-hour and 45-minute DST shifts exist
  (`Australia/Lord_Howe`, `Asia/Kathmandu`), and `fold` already defines the answer.
