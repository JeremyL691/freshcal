"""Application use cases and exit-code policy (BLUEPRINT.md §5.5, §6.1-§6.3).

This module depends only on :mod:`freshcal.core`; every outside interaction is injected
(``FreshnessReader``, ``CalendarProvider``, ``Clock``), which is what lets the use cases
be tested without a warehouse or a real clock.

Three rules shape the code and are worth stating once:

- **A source that cannot be evaluated still produces a result.** A rule that failed to
  load, a query that raised, or a reader that could not be created all become a coded
  outcome for that source — with its schedule context computed where the rule is valid,
  so the report can still say which release is at stake.
- **Exit codes have one owner:** :func:`exit_code_for`, with precedence ``2 > 3 > 1 > 0``.
- **Warnings travel with their source.** Load-time warnings (``W004`` from the merge,
  ``W006`` from a calendar without ``valid_until``) are attached to the result of the
  source they belong to.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from fnmatch import fnmatchcase

from freshcal.core.calendar import BusinessCalendar
from freshcal.core.errors import CalendarError, ConfigError, Issue, QueryError
from freshcal.core.explain import explain_lines, explanation
from freshcal.core.model import (
    CheckReport,
    EvaluationResult,
    FreshnessTarget,
    NextEntry,
    NextRelease,
    NextReport,
    RawObservation,
    SourceEntry,
    Status,
    mark_duplicate_source_ids,
)
from freshcal.core.ports import CalendarProvider, FreshnessReader
from freshcal.core.schedule import next_release_after
from freshcal.core.timeutil import local_date, to_utc
from freshcal.core.verdict import calendar_notice, evaluate, schedule_context

__all__ = [
    "ValidateReport",
    "exit_code_for",
    "merge_entries",
    "query_error_result",
    "run_check",
    "run_explain",
    "run_next",
    "run_validate",
    "select_entries",
]

_CONFIG_ERROR_FALLBACK = Issue("E209", "schedule produces no release within 1830 days")


def merge_entries(
    config_entries: Iterable[SourceEntry],
    dbt_entries: Iterable[SourceEntry],
    *,
    config_file: str = "",
) -> tuple[list[SourceEntry], list[Issue]]:
    """Combine config-file and manifest sources, sorted by source ID.

    Precedence (BLUEPRINT.md §4.8): a source defined in the config file wins **entirely**
    over the manifest definition — no field-level merge — and the run emits ``W004``
    naming both places. Duplicate IDs *inside* the manifest (the same
    ``source_name.name`` from two packages) are an error on every entry involved
    (``E206``), because nothing can say which one the operator meant.

    The returned warnings carry the affected source ID in their ``location``, so the
    application layer can attach each one to that source's result.
    """
    config_list = list(config_entries)
    config_ids = {entry.source_id for entry in config_list}
    warnings: list[Issue] = []
    merged: list[SourceEntry] = list(config_list)

    manifest_survivors: list[SourceEntry] = []
    for entry in dbt_entries:
        if entry.source_id in config_ids:
            warnings.append(
                Issue(
                    "W004",
                    f"'{entry.source_id}' is defined in both {config_file} and the dbt "
                    "manifest; using the config file definition",
                    entry.source_id,
                )
            )
            continue
        manifest_survivors.append(entry)

    merged.extend(mark_duplicate_source_ids(manifest_survivors))
    merged.sort(key=lambda entry: entry.source_id)
    return merged, warnings


def select_entries(
    entries: Sequence[SourceEntry], patterns: Sequence[str] | None
) -> list[SourceEntry]:
    """Apply ``--select`` ``fnmatch`` patterns; a pattern-free call selects everything.

    Raises ``ConfigError`` ``E210`` when patterns were given but matched nothing: a
    selection typo must fail loudly rather than silently check zero sources.
    """
    if not patterns:
        return list(entries)
    selected = [
        entry
        for entry in entries
        if any(fnmatchcase(entry.source_id, pattern) for pattern in patterns)
    ]
    if not selected:
        raise ConfigError(Issue("E210", f"--select matched no sources: {', '.join(patterns)}"))
    return selected


@dataclass(frozen=True, slots=True)
class ValidateReport:
    """What ``freshcal validate`` found: issues in report order plus the counts."""

    issues: tuple[Issue, ...]  # errors first, then warnings
    valid_count: int
    error_count: int
    warning_count: int


def exit_code_for(
    statuses: Sequence[Status], fatal_config_error: bool = False, internal_error: bool = False
) -> int:
    """Map the statuses of one run onto the exit code of §6.3.

    Precedence ``2 > 3 > 1 > 0``: configuration errors are deterministic and must be
    fixed first; runtime errors mean the verdict set is incomplete, so a "data is late"
    signal would understate the problem. ``NO_DATA`` maps to 1 because the warehouse
    answered and the expected data is absent.
    """
    if fatal_config_error or any(status is Status.CONFIG_ERROR for status in statuses):
        return 2
    if internal_error or any(status is Status.QUERY_ERROR for status in statuses):
        return 3
    if any(status in (Status.OVERDUE, Status.NO_DATA) for status in statuses):
        return 1
    return 0


def _config_error_result(
    entry: SourceEntry, now: datetime, provider: CalendarProvider | None = None
) -> EvaluationResult:
    """A ``CONFIG_ERROR`` result for an entry whose rule never loaded."""
    del provider
    issue = entry.errors[0] if entry.errors else _CONFIG_ERROR_FALLBACK
    rule = entry.rule
    result = EvaluationResult(
        source_id=entry.source_id,
        origin=entry.origin,
        status=Status.CONFIG_ERROR,
        evaluated_at=now,
        schedule_timezone=rule.schedule.timezone.key if rule is not None else None,
        release=None,
        deadline=None,
        observation=None,
        next_expected_arrival=None,
        warnings=entry.warnings,
        error=issue,
    )
    return replace(
        result,
        explanation=explanation(
            result,
            loaded_at_field=(
                rule.target.loaded_at_field if rule is not None else "loaded_at_field"
            ),
        ),
    )


def query_error_result(
    entry: SourceEntry, now: datetime, provider: CalendarProvider, issue: Issue
) -> EvaluationResult:
    """A ``QUERY_ERROR`` result, with the schedule context when the rule is valid."""
    rule = entry.rule
    if rule is None:
        return _config_error_result(entry, now, provider)
    try:
        release, next_expected = schedule_context(rule, now, provider)
    except ConfigError as error:
        broken = SourceEntry(
            source_id=entry.source_id,
            origin=entry.origin,
            rule=None,
            errors=(error.issue,),
            warnings=entry.warnings,
            location=entry.location,
        )
        return _config_error_result(broken, now, provider)

    result = EvaluationResult(
        source_id=entry.source_id,
        origin=entry.origin,
        status=Status.QUERY_ERROR,
        evaluated_at=now,
        schedule_timezone=rule.schedule.timezone.key,
        release=release,
        deadline=release.instant + rule.grace if release is not None else None,
        observation=None,
        next_expected_arrival=next_expected,
        warnings=entry.warnings,
        error=issue,
    )
    return replace(
        result, explanation=explanation(result, loaded_at_field=rule.target.loaded_at_field)
    )


def _with_warnings(result: EvaluationResult, warnings: Sequence[Issue]) -> EvaluationResult:
    """Attach warnings that name this source in their ``location``."""
    matching = tuple(issue for issue in warnings if issue.location in (None, result.source_id))
    if not matching:
        return result
    return replace(result, warnings=result.warnings + matching)


def _unusable_observation(
    target: FreshnessTarget, raw: RawObservation | None, error: BaseException
) -> Issue:
    """``E502`` for a value that cannot be turned into an instant, naming the value.

    Either the read itself failed while converting the value (``raw`` is then unknown) or
    the failure happened later, while formatting the result; both are that one source's
    data problem, never a reason to abort the run (audit E2E-02).
    """
    if raw is None or raw.value is None:
        detail = (
            f"max({target.loaded_at_field}) returned a value that cannot be used as a timestamp"
        )
    else:
        detail = (
            f"max({target.loaded_at_field}) returned {raw.value.isoformat(sep=' ')}, which "
            "cannot be used as a timestamp"
        )
    return Issue("E502", f"query failed: {detail} ({type(error).__name__}: {error})")


def run_check(
    entries: Sequence[SourceEntry],
    reader: FreshnessReader | None,
    provider: CalendarProvider,
    now: datetime,
    *,
    reader_error: Issue | None = None,
    extra_warnings: Sequence[Issue] = (),
) -> CheckReport:
    """Evaluate every entry and return the report, sorted by source ID.

    ``reader=None`` means the reader could not be created: every valid source then gets a
    ``QUERY_ERROR`` carrying ``reader_error``, while sources whose rule did not load keep
    their ``CONFIG_ERROR``.

    A failure while handling one source's value — a query error, or an overflow while
    normalizing or formatting an extreme timestamp — becomes that source's ``QUERY_ERROR``
    (``E502``); the remaining sources are still evaluated.
    """
    now = to_utc(now)
    results: list[EvaluationResult] = []
    for entry in entries:
        if entry.rule is None:
            result = _config_error_result(entry, now, provider)
        elif reader is None:
            result = query_error_result(
                entry,
                now,
                provider,
                reader_error or Issue("E501", "cannot connect to the warehouse"),
            )
        else:
            raw: RawObservation | None = None
            try:
                raw = reader.read_latest(entry.rule.target)
                result = evaluate(entry.rule, raw, now, provider)
            except QueryError as error:
                result = query_error_result(entry, now, provider, error.issue)
            except (OverflowError, ValueError) as error:
                result = query_error_result(
                    entry, now, provider, _unusable_observation(entry.rule.target, raw, error)
                )
        result = _with_warnings(result, entry.warnings)
        results.append(_with_warnings(result, extra_warnings))

    results.sort(key=lambda item: item.source_id)
    return CheckReport(
        evaluated_at=now,
        results=tuple(results),
        exit_code=exit_code_for([result.status for result in results]),
    )


def run_next(
    entries: Sequence[SourceEntry],
    provider: CalendarProvider,
    now: datetime,
    *,
    count: int = 3,
    extra_warnings: Sequence[Issue] = (),
) -> NextReport:
    """The next ``count`` releases of every entry; no warehouse is touched."""
    now = to_utc(now)
    sources = tuple(
        sorted(
            (_next_entry(entry, provider, now, count, extra_warnings) for entry in entries),
            key=lambda item: item.source_id,
        )
    )
    return NextReport(evaluated_at=now, sources=sources)


def _next_entry(
    entry: SourceEntry,
    provider: CalendarProvider,
    now: datetime,
    count: int,
    extra_warnings: Sequence[Issue],
) -> NextEntry:
    rule = entry.rule
    if rule is None:
        return NextEntry(
            source_id=entry.source_id,
            schedule_timezone=None,
            warnings=entry.warnings,
            error=entry.errors[0] if entry.errors else _CONFIG_ERROR_FALLBACK,
        )
    calendar = BusinessCalendar(rule.calendar, provider, source_id=rule.source_id)
    warnings: list[Issue] = list(entry.warnings)
    releases: list[NextRelease] = []
    error: Issue | None = None
    try:
        calendar.check_valid_at(local_date(now, rule.schedule.timezone))
        after = now
        for _ in range(count):
            release = next_release_after(rule, after, calendar)
            if release is None:
                break
            releases.append(
                NextRelease(
                    instant=release.instant,
                    local=release.local,
                    deadline=release.instant + rule.grace,
                )
            )
            after = release.instant
    except (ConfigError, CalendarError) as failure:
        error = failure.issue
        releases = []
    else:
        notice = calendar_notice(rule, calendar, now)
        if notice is not None:
            warnings.append(notice)
    warnings.extend(issue for issue in extra_warnings if issue.location in (None, rule.source_id))
    return NextEntry(
        source_id=entry.source_id,
        schedule_timezone=rule.schedule.timezone.key,
        releases=tuple(releases),
        warnings=tuple(warnings),
        error=error,
    )


def run_explain(
    entry: SourceEntry,
    raw: RawObservation,
    now: datetime,
    provider: CalendarProvider,
    *,
    origin_label: str = "",
    query_text: str | None = None,
) -> tuple[EvaluationResult, list[str]]:
    """Evaluate one source and produce its trace; returns ``(result, lines)``.

    The exit code comes from the result, exactly as for ``check`` (§6.1).
    """
    now = to_utc(now)
    if entry.rule is None:
        result = _config_error_result(entry, now, provider)
        return result, [f"Result    {result.status.value}: {result.explanation}"]
    result = _with_warnings(evaluate(entry.rule, raw, now, provider), entry.warnings)
    lines = explain_lines(
        entry.rule, raw, now, provider, origin_label=origin_label, query_text=query_text
    )
    return result, lines


def _deduplicate(issues: Iterable[Issue]) -> list[Issue]:
    """Drop repeated ``E206`` messages, keeping the first occurrence.

    Every member of a duplicate-ID group carries the same ``E206`` message (CFG-21), so
    the group is reported once. Only ``E206`` may be identical across entries: other
    messages either embed their location or genuinely describe different sources (two
    impossible cron schedules both earn the location-less ``E209``), and those must all
    be reported.
    """
    unique: list[Issue] = []
    seen_duplicates: set[str] = set()
    for issue in issues:
        if issue.code == "E206":
            if issue.message in seen_duplicates:
                continue
            seen_duplicates.add(issue.message)
        unique.append(issue)
    return unique


def run_validate(
    entries: Sequence[SourceEntry], provider: CalendarProvider, now: datetime
) -> ValidateReport:
    """Check every rule without a warehouse: schedules, calendars, and warnings (§6.2).

    Catches ``E209`` (no release in the horizon), ``E405``/``E407`` (calendar data or a
    roll failure), ``E408`` (an expired calendar) and the warnings ``W005``/``W006`` —
    plus everything a rule already carried when it was loaded.
    """
    now = to_utc(now)
    errors: list[Issue] = []
    warnings: list[Issue] = []
    entries_with_errors = 0
    valid_count = 0
    for entry in entries:
        entry_errors: list[Issue] = []
        calendar: BusinessCalendar | None = None
        if entry.rule is None:
            entry_errors.extend(entry.errors)
        else:
            rule = entry.rule
            calendar = BusinessCalendar(rule.calendar, provider, source_id=rule.source_id)
            try:
                calendar.check_valid_at(local_date(now, rule.schedule.timezone))
                schedule_context(rule, now, provider)
            except (ConfigError, CalendarError) as failure:
                entry_errors.append(failure.issue)
            else:
                # The same function `evaluate` uses, on the calendar this loop owns.
                notice = calendar_notice(rule, calendar, now)
                if notice is not None:
                    warnings.append(notice)
        errors.extend(entry_errors)
        warnings.extend(entry.warnings)
        if entry_errors:
            entries_with_errors += 1
        else:
            valid_count += 1
    return ValidateReport(
        issues=tuple(_deduplicate([*errors, *warnings])),
        valid_count=valid_count,
        error_count=entries_with_errors,
        warning_count=len(warnings),
    )
