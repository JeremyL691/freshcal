# Pull request

Related issue: <!-- e.g. #12 -->

## What this changes

<!-- Two or three sentences: the behavior that is different after this PR. -->

## Checklist

- [ ] Tests cover the change.
- [ ] `uv run ruff format . && uv run ruff check . && uv run mypy && uv run lint-imports && uv run pytest` all pass.
- [ ] No test was skipped, xfailed, or weakened to get green.
- [ ] `CHANGELOG.md` `[Unreleased]` has an entry if the change is user-visible.
- [ ] Docs (`README.md`, `docs/`) updated where behavior or configuration changed.
- [ ] No README, docs, or CHANGELOG claim that code, tests, or produced validation results do not back.
