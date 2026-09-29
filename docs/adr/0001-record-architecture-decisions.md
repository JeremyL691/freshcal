# 0001. Record architecture decisions

## Status

Accepted.

## Date

2026-09-29.

## Context

FreshCal is built autonomously from a blueprint, without a human review gate at each
step (BLUEPRINT.md §13). Decisions that shape the product — semantics, dependencies,
tooling, release policy — must be explainable to the owner afterwards, and the
reasoning must survive the context of the session that made it. Without a written
record, a later change cannot be judged as a change at all.

## Decision

We will record every architecturally significant decision as a MADR-style record in
`docs/adr/`, numbered sequentially, with the sections `Status`, `Date`, `Context`,
`Decision`, `Consequences`, and `Alternatives considered`. Each record cites the
BLUEPRINT.md section it implements. The fixed decisions of BLUEPRINT.md §1.5 and the
semantic decisions of §3 each get a record (0002–0019). Changing a decision means
adding a new record that supersedes the old one, not editing history: the superseded
record keeps its text and its status becomes `Superseded by NNNN`.

## Consequences

The set of decisions is finite and reviewable: an owner can read 20 short records
instead of 3,000 lines of blueprint. Contributors proposing a semantic change must
write a record, which forces the trade-off to be stated. The cost is a write-up per
decision and a discipline to keep records in sync with the code; T-0.4's acceptance
criteria (20 files, all sections present, every decision record citing its blueprint
section) make the discipline checkable.

## Alternatives considered

- **Comments in the code.** Rejected: decisions such as the license or the choice of
  a manifest version have no natural code location, and code comments are invisible
  to someone deciding whether to adopt the project.
- **Only the blueprint.** Rejected: the blueprint is normative and versioned as a
  whole; a dated record with alternatives is what makes a later reversal cheap.
- **A wiki or issue tracker.** Rejected: the repository must be self-contained
  (BLUEPRINT.md §10.6 — installation is from a clone).
