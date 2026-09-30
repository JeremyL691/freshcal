# Contributing to FreshCal

Thanks for helping. This project is small on purpose: FreshCal computes
freshness verdicts from declared schedules and calendars, and nothing else.

## Setup

```bash
uv sync --all-extras          # virtual environment, all optional adapters, dev tools
uv run pre-commit install     # run the checks on every commit
```

Python 3.11 or newer is required; `uv` manages the interpreter and the lockfile
(`uv.lock` is committed — do not edit it by hand).

## The checks

Every change must pass all of these, in this order (this is what CI runs):

```bash
uv run ruff format . && uv run ruff check . && uv run mypy && uv run lint-imports && uv run pytest
```

- `ruff format` / `ruff check`: formatting and lint; `TID251` bans reading the
  system clock outside `src/freshcal/adapters/clock.py` (inject a `Clock`).
- `mypy --strict` on `src/`; tests are not type-checked.
- `lint-imports`: the layering rules — `freshcal.core`
  is pure (stdlib and `croniter` only), `freshcal.app` imports only the core.
- `pytest`: PostgreSQL tests are deselected by default. Run them with
  `FRESHCAL_TEST_PG_DSN=postgresql://… uv run pytest -m postgres`; the instructions in
  `tests/integration/conftest.py` show how to start a throwaway server.
- The large differential campaign against the independent oracle
  (`tests/oracle/`, about 15 minutes) runs with `uv run pytest tests/oracle -m oracle -s`;
  CI runs it on every push. Run it before proposing any change under
  `src/freshcal/core/`.

## Test layout

| Directory | Contents |
|---|---|
| `tests/unit/core` | time utilities, calendar, schedule, observation, verdict, explanations |
| `tests/unit/config` | YAML loader, JSON Schema, rule conversion |
| `tests/unit/adapters` | clock, holidays provider, dbt manifest, reporters |
| `tests/property` | Hypothesis invariants (`HYPOTHESIS_PROFILE=ci` runs 300 examples each) |
| `tests/golden` | the normative scenarios (`scenarios.yaml`) |
| `tests/integration` | DuckDB and PostgreSQL readers, shared reader contract |
| `tests/cli` | end-to-end command-line tests and the README quickstart |
| `tests/oracle` | the independent reference implementation and the differential campaign |
| `tests/validation` | real-publication-data checks (`docs/validation.md`) |

The golden scenarios, the schemas and the specification fixtures in
`tests/fixtures/spec/` are normative: expectations come from the documented
semantics (`docs/semantics.md`), never from program output. If a golden case
looks wrong, say so in the pull request and propose an ADR rather than editing
the expectation.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/):

```
fix(core): keep the day-of-month branch of an OR cron schedule
```

Types: `feat`, `fix`, `test`, `docs`, `refactor`, `build`, `ci`, `chore`.

## Architecture decisions

Design decisions live in `docs/adr/` (MADR-style). Changing an accepted
decision or a semantic rule needs an ADR in the same pull request
(`docs/adr/0000-template.md`; `Status: Proposed` is fine). Do not change semantics inside
a feature pull request without one.

## Proposing calendar or semantic changes

Holiday and calendar data come from the `holidays` library plus user overrides;
FreshCal does not maintain its own holiday database. So:

- **Wrong or missing holiday for your region:** check the `holidays` library
  first (upstream fix), then use a calendar override (`working_days`,
  `non_working_days`, or an override file) — that is the supported path and it
  needs no change here.
- **A schedule FreshCal cannot express:** open a feature request with the
  schedule in words and the YAML you wish worked.
- **A change to what a verdict means:** this needs an ADR plus a golden
  scenario, and it changes the promise in `docs/semantics.md` and the README —
  expect a design discussion first. Naming a release-date source that proves
  the current behavior wrong is the strongest argument.

## Reporting security issues

See `SECURITY.md`. Do not open a public issue for a vulnerability.
