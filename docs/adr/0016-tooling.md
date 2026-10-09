# 0016. Tooling: uv, hatchling, ruff, mypy, import-linter, argparse, jsonschema

## Status

Accepted.

## Date

2026-09-29.

## Context

The toolchain must be reproducible on a macOS development machine and on
`ubuntu-latest` CI, keep the commands identical everywhere, and add as little
dependency surface as possible: every extra runtime dependency is something a user
must trust and a maintainer must track. Python packaging in 2026 has several
overlapping choices (pip/venv/tox, poetry/pdm/hatch/uv, black+isort/flake8/ruff,
click/typer/argparse), and the choice affects reproducibility, CI time, and how much
of the project is tool-specific.

## Decision

uv manages interpreters, the environment, and the committed lockfile; hatchling is the
build backend (PEP 621 metadata, PEP 639 license expression, includes
`src/freshcal/schemas/*.json` as package data); ruff does both lint and format, with
`TID251` encoding the clock rule (ADR 0010); mypy strict checks `src/` (ADR 0004);
import-linter enforces the layering (ADR 0003); `argparse` implements the CLI (stdlib,
so exit codes are fully under our control); `jsonschema` validates config and reports
because a maintained Draft 2020-12 implementation is not something to rewrite
(§10.1–§10.3, §12.1). The four check commands are one line and
run identically locally, in pre-commit, and in CI.

## Consequences

One tool per job, a single lockfile, and no tool-specific source layout: the package
is a plain `src/` layout that any PEP 517 tool can build, and `uv build`'s artifacts
pass `twine check --strict`. Version bumps are visible in one file. The costs: uv is a
young tool whose lockfile format changes across majors (CI pins the action by commit
SHA), coverage of `jsonschema` errors had to be mapped to our own issue
codes rather than emitted raw, and `argparse` means writing help text and validation by
hand instead of using decorators — accepted because the exit-code contract matters
more than ergonomics.

## Alternatives considered

- **poetry / pdm / hatch environments.** Rejected: slower CI, overlapping features, and
  no advantage over uv's lockfile plus a CI matrix.
- **black + isort + flake8.** Rejected: three tools and three configs; ruff replaces
  them and can encode the `TID251` ban.
- **Typer/Click for the CLI.** Rejected: an extra dependency, less direct control of
  exit codes (argparse's usage errors already exit 2, matching our config-error code),
  and four subcommands do not need a framework.
- **A hand-written validator instead of JSON Schema.** Rejected: the schema is also the
  editor experience (`yaml-language-server`), and its design constraints are already
  characterised against jsonschema 4.26.
