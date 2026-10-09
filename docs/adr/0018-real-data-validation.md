# 0018. Real-data validation against public publication histories

## Status

Accepted.

## Date

2026-09-29.

## Context

Golden tests prove that the code matches the design specification; they cannot prove
the specification matches the world. The risk that matters most for this project is a
calendar or schedule model that is internally consistent but wrong about when a
publisher actually publishes. Two kinds of evidence are available from public
sources: complete publication *date* histories (the ECB publishes a definitive list
since 1999; the US Treasury publishes daily par yield curves) and a publication-*time*
proxy (the `Last-Modified` header of the ECB's daily file, which RFC 9110 defines as
the server's belief about the last modification — not proof the file was public then).

## Decision

v0.1 ships both, and only results actually produced may be quoted. (A) Automated, in CI: `tests/validation/test_calendar_history.py`
compares FreshCal's own `releases_between` against the committed ECB and Treasury date
lists and asserts exact confusion-matrix counts, including the Treasury case where
`country: US` and `XNYS` both fail and overrides fix it — the evidence for ADR 0006.
(B) Arrival times: a stdlib collector records the `(rate_date, Last-Modified)`
pair and the first response `Date` whenever it runs, and the replay script checks past
runs against that proxy, reporting agreement, never "accuracy". With fewer than 20 collected
business days the report says "insufficient data" and the README makes no timing claim.

## Consequences

Calendar claims are regression-tested against 7,102 ECB dates and 749 Treasury dates,
so a `holidays` release that changes any of them fails the build and forces an
investigation instead of a silent behavior change. Timing claims are bounded by the
honesty rules: the replay must list every disagreement with an explanation (for
example a proxy time before the configured release time, which the ECB's 15:56:44 CEST
observation on 2026-09-28 already demonstrates), and it must state that it measures
agreement with a proxy. The cost is committed third-party data with attribution, a
collector whose data volume depends on how often a session runs, and a
documentation obligation to keep `docs/validation.md` in step with what was produced.

## Alternatives considered

- **Only golden tests.** Rejected: they encode the specification's assumptions, so a
  wrong assumption stays wrong; validation is the only outside check available without a
  production deployment.
- **Claim arrival accuracy from `Last-Modified`.** Rejected: the header is the server's
  modification time with a 5-minute cache window and same-day revisions; calling that
  accuracy would be an unbacked claim.
- **Use a paid market-data provider as ground truth.** Rejected: cost and licensing;
  public sources already give a definitive date history.
- **Validate against the user's own data instead.** Rejected: v0.1 has no users; the
  README instead recommends running in shadow mode next to existing checks.
