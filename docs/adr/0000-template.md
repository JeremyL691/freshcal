# 0000. Template

## Status

Template. Copy this file to `NNNN-<kebab-case-title>.md`, take the next free number, and fill in every section.

## Date

YYYY-MM-DD (the date the decision was made, not the date it was written up).

## Context

What forces are at play: the problem, the constraints (technical, operational, contractual), and what is at stake. State facts with their source and the date they were checked (for example "verified with library version X on YYYY-MM-DD"). Describe the situation, not the solution.

## Decision

The decision in the active voice: "We will ...". Include the boundaries — what this decision does *not* cover — and the rule an implementer must follow. Cite the blueprint section it records (`BLUEPRINT.md §n`).

## Consequences

What becomes easier, what becomes harder, and what is now constrained (tests, documentation, dependencies, migration). Positive and negative. Name the evidence that keeps the decision honest: tests, validation data, or a drift guard.

## Alternatives considered

Each alternative that was seriously weighed, with the reason it was not chosen. "We did not consider anything else" is not acceptable; if the alternative is a well-known tool or approach, say why it loses here.
