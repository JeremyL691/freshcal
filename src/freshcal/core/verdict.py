"""Verdict and outcome algorithm (BLUEPRINT.md §3.7).

The whole evaluation rests on one monotonicity fact: because grace is constant, the
deadlines of consecutive releases are strictly increasing, so the arrived releases are
a prefix of the release sequence and the missed ones are a prefix of the unarrived
ones. Everything therefore depends on the **first unarrived release** *U* — the
earliest release after the observed timestamp — and on where *now* sits relative to
its deadline.

Two properties are worth stating because the code is arranged around them:

- ``ON_TIME`` is never concluded from a partial search. When the observed timestamp is
  older than the search horizon, the search first looks for a missed release in the
  recent window (which proves OVERDUE), then searches forward from the observed
  timestamp itself (which finds the exact oldest miss), and otherwise returns ``E215``
  — "cannot decide" — rather than a verdict.
- Counting stops at ``MAX_COUNTED_RELEASES``. Truncation never changes the status,
  because the status depends only on *U*; it only makes the counts lower bounds.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, time, timedelta

from freshcal.core.calendar import BusinessCalendar
from freshcal.core.errors import CalendarError, ConfigError, Issue
from freshcal.core.explain import explanation
from freshcal.core.model import (
    EvaluationResult,
    Observation,
    RawObservation,
    Release,
    SourceRule,
    Status,
)
from freshcal.core.observation import normalize_observed
from freshcal.core.ports import CalendarProvider
from freshcal.core.schedule import (
    CHUNK,
    MAX_COUNTED_RELEASES,
    SEARCH_HORIZON,
    next_release_after,
    previous_release_at_or_before,
    releases_between,
)
from freshcal.core.timeutil import local_date, resolve_local, to_utc

__all__ = ["evaluate", "find_first_unarrived", "iter_releases", "schedule_context"]


def iter_releases(
    rule: SourceRule, start: datetime, end: datetime, calendar: BusinessCalendar
) -> Iterator[Release]:
    """Releases in ``[start, end]``, produced window by window (never one big list)."""
    lo = to_utc(start)
    limit = to_utc(end)
    while lo <= limit:
        hi = min(lo + CHUNK, limit)
        yield from releases_between(rule, lo, hi, calendar)
        lo = hi + timedelta(microseconds=1)


def _take(iterator: Iterator[Release], count: int) -> list[Release]:
    taken: list[Release] = []
    for release in iterator:
        taken.append(release)
        if len(taken) == count:
            break
    return taken


def find_first_unarrived(
    rule: SourceRule,
    start: datetime,
    inclusive: bool,
    now: datetime,
    calendar: BusinessCalendar,
) -> tuple[Release | None, bool]:
    """The earliest release after ``start`` that is at or before ``now``, or ``None``.

    ``None`` is returned only when the whole interval ``(start, now]`` was searched.
    The second element says whether counting was truncated, that is whether the result
    came from step 1 (the recent window) rather than from a complete search.
    """
    now = to_utc(now)
    start = to_utc(start)
    if start >= now - SEARCH_HORIZON:
        # Normal case: one bounded, complete search covers (start, now].
        return next_release_after(rule, start, calendar, inclusive=inclusive, until=now), False

    # Stale beyond the horizon. Step 1: a missed release inside the last SEARCH_HORIZON
    # proves OVERDUE, because it lies after `start` (unarrived) and past its deadline.
    recent = next_release_after(rule, now - SEARCH_HORIZON, calendar, inclusive=True, until=now)
    if recent is not None and now > recent.instant + rule.grace:
        return recent, True
    # Step 2: search forward from `start` itself, which is contiguous and therefore
    # finds the exact oldest unarrived release.
    first = next_release_after(
        rule, start, calendar, inclusive=inclusive, until=start + SEARCH_HORIZON
    )
    if first is not None:
        return first, False
    raise ConfigError(
        Issue(
            "E215",
            f"cannot decide freshness: observed timestamp {_format(start)} is more than "
            "1830 days old, no release in the last 1830 days has passed its deadline, and "
            "the schedule has no release within 1830 days after the observed timestamp",
        )
    )


def _format(value: datetime) -> str:
    return to_utc(value).isoformat().replace("+00:00", "Z")


def schedule_context(
    rule: SourceRule, now: datetime, provider: CalendarProvider
) -> tuple[Release | None, datetime | None]:
    """The latest release at or before ``now`` and the next expected arrival.

    Used for the ``QUERY_ERROR`` path, where the rule is valid but the warehouse did not
    answer: the report still shows which release the verdict would have been about.
    """
    now = to_utc(now)
    calendar = BusinessCalendar(rule.calendar, provider, source_id=rule.source_id)
    last = previous_release_at_or_before(rule, now, calendar)
    following = next_release_after(rule, now, calendar)
    if last is None and following is None:
        raise ConfigError(
            Issue(
                "E209",
                f"schedule produces no release within 1830 days before or after {_format(now)}",
            )
        )
    return last, (following.instant if following is not None else None)


def _result(
    rule: SourceRule,
    now: datetime,
    status: Status,
    calendar: BusinessCalendar,
    *,
    release: Release | None = None,
    observation: Observation | None = None,
    next_expected_arrival: datetime | None = None,
    missed_count: int = 0,
    pending_count: int = 0,
    missed_truncated: bool = False,
    warnings: tuple[Issue, ...] = (),
    error: Issue | None = None,
) -> EvaluationResult:
    """Assemble the result: deadline from the release, W005 from the calendar."""
    deadline = release.instant + rule.grace if release is not None else None
    consulted = calendar.consulted_past_valid_until()
    valid_until = calendar.spec.valid_until
    if consulted is not None and valid_until is not None:
        warnings = (
            *warnings,
            Issue(
                "W005",
                f"calendar {calendar.label} was consulted for {consulted.isoformat()}, after "
                f"its valid_until {valid_until.isoformat()}; the next expected arrival may "
                "be wrong",
                rule.source_id,
            ),
        )
    result = EvaluationResult(
        source_id=rule.source_id,
        origin=rule.origin,
        status=status,
        evaluated_at=now,
        schedule_timezone=rule.schedule.timezone.key,
        release=release,
        deadline=deadline,
        observation=observation,
        next_expected_arrival=next_expected_arrival,
        missed_count=missed_count,
        missed_truncated=missed_truncated,
        pending_count=pending_count,
        explanation="",
        warnings=warnings,
        error=error,
    )
    return replace(
        result,
        explanation=explanation(result, loaded_at_field=rule.target.loaded_at_field),
    )


def evaluate(
    rule: SourceRule,
    raw: RawObservation,
    now: datetime,
    calendar_provider: CalendarProvider,
) -> EvaluationResult:
    """Evaluate one source: normalize the value, find the first unarrived release, decide."""
    now = to_utc(now)
    timezone = rule.schedule.timezone
    calendar = BusinessCalendar(rule.calendar, calendar_provider, source_id=rule.source_id)
    try:
        calendar.check_valid_at(local_date(now, timezone))  # E408 when past valid_until
        observation, normalization_warnings = normalize_observed(
            raw,
            rule.observed_timezone,
            now,
            loaded_at_field=rule.target.loaded_at_field,
        )
        warnings: tuple[Issue, ...] = tuple(
            Issue(issue.code, issue.message, rule.source_id) for issue in normalization_warnings
        )

        last = previous_release_at_or_before(rule, now, calendar)
        following = next_release_after(rule, now, calendar)
        if last is None and following is None:
            raise ConfigError(
                Issue(
                    "E209",
                    f"schedule produces no release within 1830 days before or after {_format(now)}",
                )
            )
        next_expected_arrival = following.instant if following is not None else None

        if observation is None and rule.active_from is None:
            return _result(
                rule,
                now,
                Status.NO_DATA,
                calendar,
                release=last,
                next_expected_arrival=next_expected_arrival,
                warnings=warnings,
            )

        if observation is None:
            # Empty table, but `active_from` says when data started to be due: releases
            # from the floor onwards count, and the floor itself is inclusive.
            assert rule.active_from is not None
            start = resolve_local(datetime.combine(rule.active_from, time.min), timezone)
            inclusive = True
        else:
            start = observation.instant
            inclusive = False

        first_unarrived, truncated = find_first_unarrived(rule, start, inclusive, now, calendar)
        if first_unarrived is None:
            return _result(
                rule,
                now,
                Status.ON_TIME,
                calendar,
                release=last,
                observation=observation,
                next_expected_arrival=next_expected_arrival,
                warnings=warnings,
            )

        unarrived = _take(
            iter_releases(rule, first_unarrived.instant, now, calendar), MAX_COUNTED_RELEASES
        )
        truncated = truncated or len(unarrived) == MAX_COUNTED_RELEASES
        missed = [release for release in unarrived if now > release.instant + rule.grace]
        pending = [release for release in unarrived if now <= release.instant + rule.grace]
        if missed:
            return _result(
                rule,
                now,
                Status.OVERDUE,
                calendar,
                release=missed[0],
                observation=observation,
                next_expected_arrival=next_expected_arrival,
                missed_count=len(missed),
                pending_count=len(pending),
                missed_truncated=truncated,
                warnings=warnings,
            )
        return _result(
            rule,
            now,
            Status.NOT_DUE,
            calendar,
            release=pending[0],
            observation=observation,
            next_expected_arrival=next_expected_arrival,
            pending_count=len(pending),
            missed_truncated=truncated,
            warnings=warnings,
        )
    except (ConfigError, CalendarError) as error:
        return _result(
            rule,
            now,
            Status.CONFIG_ERROR,
            calendar,
            error=Issue(error.issue.code, error.issue.message, rule.source_id),
        )
