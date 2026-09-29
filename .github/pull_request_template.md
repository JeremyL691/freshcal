# Pull request

Task ID (BLUEPRINT.md §12): <!-- e.g. T-2.2 -->

## What this changes

<!-- Two or three sentences: the behavior that is different after this PR. -->

## Checklist

- [ ] Tests cover the change, and every acceptance criterion of the task passes locally.
- [ ] `uv run ruff format . && uv run ruff check . && uv run mypy && uv run lint-imports && uv run pytest` all pass.
- [ ] No test was skipped, xfailed, or weakened to get green (or the reason is recorded in PROGRESS.md).
- [ ] `CHANGELOG.md` `[Unreleased]` has an entry if the change is user-visible.
- [ ] Docs (`README.md`, `docs/`) updated where behavior or configuration changed.
- [ ] No README, docs, or CHANGELOG claim that code, tests, or produced validation results do not back.
- [ ] `PROGRESS.md` updated in the same commit (status, summary, deviations).
