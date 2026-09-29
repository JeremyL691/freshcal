# 0017. License choice

## Status

Accepted. Chosen by applying the default of owner decision 3 (BLUEPRINT.md §14.4); the
owner may still change it before v0.1.0, which is cheap while no release exists.

## Date

2026-09-29.

## Context

FreshCal is meant to be adopted inside company data stacks, and it links no
GPL-family code at runtime beyond one optional dependency. The owner did not answer
decision 3 ("License") before T-0.1 started, so the documented default applies:
Apache-2.0 (BLUEPRINT.md §14.4, §10.7). The main alternative in this space is MIT,
which is shorter and equally permissive but silent on patents and on contribution
terms.

## Decision

FreshCal is licensed under **Apache-2.0**. `LICENSE` is the verbatim text from
`https://www.apache.org/licenses/LICENSE-2.0.txt` (fetched 2026-09-29 and byte-compared
in the task's acceptance criteria), and `pyproject.toml` declares
`license = "Apache-2.0"` with `license-files = ["LICENSE"]` (PEP 639). Runtime
dependencies are permissive (croniter MIT, holidays MIT, PyYAML MIT, jsonschema MIT,
DuckDB MIT); psycopg is LGPL-3.0-only and stays an optional, separately installed extra
(§1.5 F7), which the blueprint treats as compatible with an Apache-2.0 FreshCal — an
owner assumption, not legal advice (§14.2 item 8).

## Consequences

Users get an explicit patent grant and a contribution-licensing clause, which is the
norm in the data tooling ecosystem (dbt-core declares Apache-2.0). The license file is
longer than MIT's, and a future relicense to MIT would need an ADR plus a check with
contributors. The ADR records which decision was applied and when, so the owner's
answer — if it differs — supersedes this record rather than being lost in chat.

## Alternatives considered

- **MIT.** Rejected only because Apache-2.0 is the documented default: MIT is equally
  permissive but says nothing about patents, which matters more for a tool companies
  embed in pipelines.
- **AGPL or another copyleft license.** Rejected: it would prevent the intended
  adoption inside private data platforms.
- **No license (all rights reserved) until the owner answers.** Rejected: without a
  license the code is not usable by anyone, and every task from T-0.1 on would produce
  unlicensed artifacts.
