# 0001. Record architecture decisions

## Status

Accepted.

## Date

2026-09-29.

## Context

FreshCal is developed from a written design specification. Decisions that shape the product — semantics, dependencies,
tooling, release policy — must be explainable to maintainers and users afterwards, and the
reasoning must survive the context of the session that made it. Without a written
record, a later change cannot be judged as a change at all.

## Decision

We will record every architecturally significant decision as a MADR-style record in
`docs/adr/`, numbered sequentially, with the sections `Status`, `Date`, `Context`,
`Decision`, `Consequences`, and `Alternatives considered`. The fixed product decisions and
the semantic decisions each get a record (0002–0018). Changing a decision means
adding a new record that supersedes the old one, not editing history: the superseded
record keeps its text and its status becomes `Superseded by NNNN`.

## Consequences

The set of decisions is finite and reviewable: an owner can read 20 short records
instead of 3,000 lines of specification. Contributors proposing a semantic change must
write a record, which forces the trade-off to be stated. The cost is a write-up per
decision and a discipline to keep records in sync with the code; the acceptance
criteria for the records (20 files, all sections present, every decision record citing
its specification section) make the discipline checkable.

## Alternatives considered

- **Comments in the code.** Rejected: decisions such as the license or the choice of
  a manifest version have no natural code location, and code comments are invisible
  to someone deciding whether to adopt the project.
- **Only the design specification.** Rejected: the specification is versioned as a
  whole; a dated record with alternatives is what makes a later reversal cheap.
- **A wiki or issue tracker.** Rejected: the repository must be self-contained
  (installation works from a clone).
