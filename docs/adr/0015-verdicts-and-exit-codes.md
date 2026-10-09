# 0015. Verdicts, non-verdict outcomes, and exit codes

## Status

Accepted.

## Date

2026-09-29.

## Context

A freshness check must be usable by both a human and an automated caller (a cron job,
a CI step, an alerting rule). The engine can be unable to answer for three different
reasons — the rule itself is invalid, the warehouse query failed, or there is no data
at all — and collapsing those into ON_TIME or OVERDUE would make a broken check look
like a healthy or a late source. The classic failure of a freshness tool is the silent
false green: a mis-configuration that produces exit 0 forever.

## Decision

Every evaluation produces exactly one status: a verdict (`ON_TIME`, `NOT_DUE`,
`OVERDUE`) or a non-verdict outcome (`NO_DATA`, `CONFIG_ERROR`, `QUERY_ERROR`), chosen
by the decision table of whose rows are mutually exclusive and
exhaustive by construction. Only rows 4–6 are verdicts
(`is_verdict`). Errors that can be attributed to one source leave that source in
CONFIG_ERROR and the rest of the run proceeds. Exit codes are derived by one function:
0 when every evaluated source is ON_TIME or NOT_DUE, 1 for OVERDUE or NO_DATA, 2 for
configuration errors or CLI misuse, 3 for runtime errors, with precedence 2 > 3 > 1 > 0
(§6.3). Errors are coded (`E…`) with a location and a message, and warnings (`W…`)
never change the exit code.

## Consequences

Automated callers get a small, documented contract; a configuration mistake exits 2
with a coded message instead of pretending to be a verdict; a warehouse failure exits 3
so an alert can distinguish "the data is late" from "the check could not run". The
costs are visible in the report: a run can mix verdicts and non-verdicts, so consumers
must read `status` rather than assume a single shape, and `NO_DATA` mapped to exit 1 is
a deliberate judgement that a missing table matters more than an infrastructure
classification. Tests: U-VER-01–U-VER-14, U-APP-08 (the full exit-code table), and the
golden non-verdict rows G20b, G34, G37.

## Alternatives considered

- **Two outcomes (fresh/stale) with the reason in a log line.** Rejected: the reason is
  what determines the response, and a log line is not assertable by a caller.
- **Exit 0 for NO_DATA (empty table is not an error).** Rejected: an empty table is
  precisely the incident a freshness check exists to catch; §3.8.15 and §6.3 classify
  it as a failure. Users who expect a source to be empty use `active_from` semantics.
- **Exit 1 for everything that is not ON_TIME.** Rejected: configuration errors are
  deterministic and must be fixed before any verdict is trusted, which is why 2
  outranks 1.
- **Warnings that change the exit code.** Rejected: it makes W-codes a second,
  redundant error channel; the E214 change in design revision 1.1 moved the one case
  that mattered (guessing a zone) into an error.
