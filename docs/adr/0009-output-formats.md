# 0009. Terminal table and versioned JSON report

## Status

Accepted.

## Date

2026-09-29.

## Context

FreshCal's output has two audiences: a data engineer reading a terminal or a CI log,
and an automated caller that must act on the result. The automated caller needs a
stable contract — the exit code alone cannot say which source is late or by how much —
while the human needs a table that shows one line per source and a sentence
explaining the verdict. Output that changes shape between releases breaks integrations;
output that is only human-readable cannot be asserted in tests.

## Decision

v0.1 has exactly two renderers over the same `CheckReport`: a plain-text table on
stdout (no colour, no box drawing, `-` for missing values) and a JSON document with
`schema_version: "1.0"`, 2-space indentation, sorted results, and its own committed
JSON Schema (§8.1–§8.4). Additive changes bump the minor
version; removing or renaming a field or changing status semantics bumps the major
version and requires a new ADR. The explanation sentence is part of the contract and
is normative, so the human and the automated view can never disagree about what a
status means. Statuses mean exactly what §3.7.3 says — ON_TIME is a current-state
verdict, not a punctuality record.

## Consequences

Both renderers are testable: the table against the worked example in §8.3, the JSON
against the example in §8.2 and against the schema on every run, with drift guards
comparing the committed schemas to the blueprint's blocks. Slack, Elementary, and
other formats are additive work behind the same `Reporter` port (roadmap items 3–4).
The cost is discipline: any new field must be additive and schema-validated, and a
semantic change must be recorded rather than slipped into the output.

## Alternatives considered

- **CSV.** Rejected: no place for nested values (release metadata, warnings), and no
  schema story.
- **YAML output.** Rejected: no parser advantage over JSON and a looser contract.
- **Rich/colour table.** Rejected: CI logs and piping are the primary consumers; ANSI
  escapes and Unicode box characters add failure modes without adding information.
- **JSON only.** Rejected: the primary use case is a human deciding whether to
  investigate, and a table with a per-source sentence is what makes that fast.
