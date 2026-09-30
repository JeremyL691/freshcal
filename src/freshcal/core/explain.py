"""Explanation sentences and the explain trace.

The sentence is normative: it is part of the JSON contract, so a reader of a report and
a reader of the terminal output must see the same claim about the same status. The
trace is not normative in wording or spacing — it exists so that a human can see which
releases were considered, which local dates were skipped and why, and which comparison
produced the status.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from freshcal.core.calendar import BusinessCalendar
from freshcal.core.errors import CalendarError, ConfigError
from freshcal.core.model import (
    BusinessDaysSchedule,
    CronSchedule,
    EvaluationResult,
    NonBusinessDayPolicy,
    RawObservation,
    Release,
    SourceRule,
    Status,
)
from freshcal.core.ports import CalendarProvider
from freshcal.core.schedule import next_release_after, previous_release_at_or_before
from freshcal.core.timeutil import format_duration, format_local, format_utc, to_utc

__all__ = ["explain_lines", "explanation"]

NO_NEXT_RELEASE = "no next release within 1830 days"
MAX_GAP_LINES = 10


def _zone(result: EvaluationResult) -> ZoneInfo | None:
    return ZoneInfo(result.schedule_timezone) if result.schedule_timezone else None


def _missed_text(missed_count: int, truncated: bool) -> str:
    # A truncated count keeps its plural but a single miss is
    # singular — `at least 1 release missed`, never `at least 1 releases missed`.
    if truncated:
        noun = "release" if missed_count == 1 else "releases"
        return f"at least {missed_count} {noun} missed"
    if missed_count == 1:
        return "1 release missed"
    return f"{missed_count} releases missed"


def explanation(result: EvaluationResult, *, loaded_at_field: str = "loaded_at_field") -> str:
    """The one-sentence explanation of a result, per the documented templates."""
    timezone = _zone(result)

    def fmt(value: datetime) -> str:
        if timezone is None:  # pragma: no cover - a result with a release always has a zone
            return format_utc(value)
        return format_local(value, timezone)

    next_part = (
        f"next release {fmt(result.next_expected_arrival)}"
        if result.next_expected_arrival is not None
        else NO_NEXT_RELEASE
    )

    if result.status is Status.ON_TIME:
        if result.release is not None and result.observation is not None:
            return (
                f"On time: latest release {fmt(result.release.instant)} arrived "
                f"(observed {fmt(result.observation.instant)}); {next_part}."
            )
        return f"On time: no release has occurred yet; {next_part}."

    if result.status is Status.NOT_DUE and result.release is not None:
        pending = "" if result.pending_count == 1 else f" ({result.pending_count} releases pending)"
        deadline = result.deadline or result.release.instant
        return (
            f"Not due: release {fmt(result.release.instant)} has not arrived yet{pending}; "
            f"grace window ends {fmt(deadline)}; {next_part}."
        )

    if result.status is Status.OVERDUE and result.release is not None:
        deadline = result.deadline or result.release.instant
        observed = fmt(result.observation.instant) if result.observation is not None else "never"
        return (
            f"Overdue: release {fmt(result.release.instant)} missed its deadline "
            f"{fmt(deadline)}; {_missed_text(result.missed_count, result.missed_truncated)}; "
            f"latest data observed {observed}."
        )

    if result.status is Status.NO_DATA:
        latest = fmt(result.release.instant) if result.release is not None else "none"
        return (
            f"No data: max({loaded_at_field}) returned NULL; latest release {latest}; {next_part}."
        )

    if result.status is Status.CONFIG_ERROR:
        code = result.error.code if result.error is not None else "E599"
        message = result.error.message if result.error is not None else ""
        return f"Configuration error: {code} {message}"

    code = result.error.code if result.error is not None else "E599"
    message = result.error.message if result.error is not None else ""
    return f"Query error: {code} {message}"


def _schedule_description(rule: SourceRule) -> str:
    schedule = rule.schedule
    if isinstance(schedule, CronSchedule):
        policy = (
            ""
            if schedule.on_non_business_day is NonBusinessDayPolicy.NONE
            else f" (on_non_business_day: {schedule.on_non_business_day.value})"
        )
        return f"cron '{schedule.expression}' {schedule.timezone.key}{policy}"
    if isinstance(schedule, BusinessDaysSchedule):
        return f"business_days at {schedule.at.strftime('%H:%M')} {schedule.timezone.key}"
    return (
        f"monthly_business_day {schedule.business_day} at "
        f"{schedule.at.strftime('%H:%M')} {schedule.timezone.key}"
    )


def _calendar_description(rule: SourceRule) -> str:
    spec = rule.calendar
    weekend = ", ".join(
        weekday.name.lower() for weekday in sorted(spec.weekend, key=lambda day: day.value)
    )
    holidays = (
        ", ".join(ref.label() for ref in spec.holiday_calendars)
        if spec.holiday_calendars
        else "none"
    )
    overrides: list[str] = []
    if spec.extra_working_days:
        overrides.append(
            "working: " + ", ".join(day.isoformat() for day in sorted(spec.extra_working_days))
        )
    if spec.extra_non_working_days:
        overrides.append(
            "non-working: "
            + ", ".join(day.isoformat() for day in sorted(spec.extra_non_working_days))
        )
    override_text = " | ".join(overrides) if overrides else "none"
    valid_until = f" | valid_until: {spec.valid_until.isoformat()}" if spec.valid_until else ""
    return (
        f"weekend: {weekend or 'none'} | holidays: {holidays} | overrides: {override_text}"
        f"{valid_until}"
    )


def _release_state(
    release: Release,
    now: datetime,
    deadline: datetime,
    observation_instant: datetime | None,
    *,
    timezone: ZoneInfo,
    is_next: bool,
) -> str:
    """One release's row state. Only the next expected arrival carries the tag."""
    if observation_instant is not None and observation_instant >= release.instant:
        return "arrived"
    if release.instant > now:
        return "future (next expected arrival)" if is_next else "future"
    state = "not passed" if now <= deadline else "passed"
    return f"occurred, not arrived; deadline {format_local(deadline, timezone)} {state}"


def _month_days(year: int, month: int) -> Iterator[date]:
    day = date(year, month, 1)
    while day.month == month:
        yield day
        day += timedelta(days=1)


def _clamped_text(calendar: BusinessCalendar, local: datetime) -> str:
    """``clamped: February 2026 has only 20 business days``."""
    count = sum(1 for day in _month_days(local.year, local.month) if calendar.is_business_day(day))
    return f"clamped: {local.strftime('%B %Y')} has only {count} business days"


def explain_lines(
    rule: SourceRule,
    observation: RawObservation,
    now: datetime,
    provider: CalendarProvider,
    *,
    origin_label: str = "",
    query_text: str | None = None,
) -> list[str]:
    """The step-by-step trace printed by ``freshcal explain``.

    ``origin_label`` is built by the caller (it names the config file and the entry, for
    example ``config freshcal.yml, sources[0]``) and ``query_text`` is the SQL the
    reader would run, so the core stays free of paths and adapters.
    """
    # Imported here: verdict.py imports `explanation` from this module, so a module-level
    # import would be circular.
    from freshcal.core.verdict import evaluate

    now = to_utc(now)
    timezone = rule.schedule.timezone
    result = evaluate(rule, observation, now, provider)
    calendar = BusinessCalendar(rule.calendar, provider, source_id=rule.source_id)

    def fmt(value: datetime) -> str:
        return format_local(value, timezone)

    lines: list[str] = []
    lines.append(
        f"Source    {rule.source_id} ({origin_label})"
        if origin_label
        else f"Source    {rule.source_id}"
    )
    lines.append(f"Schedule  {_schedule_description(rule)}")
    lines.append(f"Calendar  {_calendar_description(rule)}")
    lines.append(f"Grace     {format_duration(rule.grace)} (wall-clock)")
    if query_text is not None:
        lines.append(f"Query     {query_text}")
    lines.append(f"Now       {format_utc(now)} = {fmt(now)}")

    normalized = result.observation
    if normalized is None:
        lines.append("Observed  NULL")
    elif normalized.was_naive:
        lines.append(
            f"Observed  {format_utc(normalized.instant)} = {fmt(normalized.instant)} "
            f"(naive value interpreted in {normalized.interpreted_timezone} via observed_timezone)"
        )
    else:
        lines.append(
            f"Observed  {format_utc(normalized.instant)} = {fmt(normalized.instant)} "
            "(timezone-aware value)"
        )

    lines.append("")
    lines.append(f"Releases around now ({timezone.key})")
    try:
        lines.extend(
            _surrounding_releases(
                rule,
                now,
                calendar,
                observation_instant=normalized.instant if normalized is not None else None,
            )
        )
    except (ConfigError, CalendarError) as error:
        lines.append(f"  cannot list releases: {error.issue.code} {error.issue.message}")

    lines.append("")
    lines.append("Reasoning")
    lines.extend(_reasoning(result, now, timezone))
    lines.append(f"  => {result.status.value}")
    lines.append("")
    lines.append(f"Result    {result.status.value}: {result.explanation}")
    return lines


def _surrounding_releases(
    rule: SourceRule,
    now: datetime,
    calendar: BusinessCalendar,
    *,
    observation_instant: datetime | None,
) -> list[str]:
    lines: list[str] = []
    latest: list[Release] = []
    candidate = previous_release_at_or_before(rule, now, calendar)
    for _ in range(3):
        if candidate is None:
            break
        latest.append(candidate)
        candidate = previous_release_at_or_before(
            rule, candidate.instant - timedelta(microseconds=1), calendar
        )
    following: list[Release] = []
    candidate = next_release_after(rule, now, calendar)
    for _ in range(2):
        if candidate is None:
            break
        following.append(candidate)
        candidate = next_release_after(rule, candidate.instant, calendar)

    ordered = list(reversed(latest)) + following
    next_expected = following[0].instant if following else None
    previous_date: date | None = None
    for release in ordered:
        if previous_date is not None:
            lines.extend(_gap_lines(rule, calendar, previous_date, release.local.date()))
        deadline = release.instant + rule.grace
        state = _release_state(
            release,
            now,
            deadline,
            observation_instant,
            timezone=rule.schedule.timezone,
            is_next=release.instant == next_expected,
        )
        annotations: list[str] = []
        if release.adjusted_from is not None:
            annotations.append(f"adjusted from {release.adjusted_from.isoformat()}")
        if release.dst == "gap":
            annotations.append(f"DST gap: shifted to {release.local.strftime('%H:%M')}")
        elif release.dst == "ambiguous":
            annotations.append("DST overlap: first occurrence")
        if release.clamped:
            annotations.append(_clamped_text(calendar, release.local))
        suffix = f" ({'; '.join(annotations)})" if annotations else ""
        lines.append(
            f"  {release.local.strftime('%a %Y-%m-%d %H:%M %Z')}  "
            f"{format_utc(release.instant)}  {state}{suffix}"
        )
        previous_date = release.local.date()
    if not ordered:
        lines.append("  no releases within the search horizon")
    return lines


def _gap_lines(rule: SourceRule, calendar: BusinessCalendar, start: date, end: date) -> list[str]:
    """Non-business local dates between two releases, with their reason."""
    if not isinstance(rule.schedule, BusinessDaysSchedule) and not (
        isinstance(rule.schedule, CronSchedule)
        and rule.schedule.on_non_business_day is not NonBusinessDayPolicy.NONE
    ):
        return []
    lines: list[str] = []
    day = start + timedelta(days=1)
    while day < end and len(lines) < MAX_GAP_LINES:
        reason = calendar.non_business_reason(day)
        if reason is not None:
            lines.append(f"  {day.strftime('%a %Y-%m-%d')}  no release: {reason}")
        day += timedelta(days=1)
    return lines


def _reasoning(result: EvaluationResult, now: datetime, timezone: ZoneInfo) -> list[str]:
    def fmt(value: datetime) -> str:
        return format_local(value, timezone)

    if result.status is Status.CONFIG_ERROR:
        return [
            f"  1. {result.error.code} {result.error.message}"
            if result.error
            else "  1. configuration error"
        ]
    if result.status is Status.QUERY_ERROR:
        return [
            f"  1. {result.error.code} {result.error.message}"
            if result.error
            else "  1. query error"
        ]
    if result.status is Status.NO_DATA:
        return [
            "  1. The warehouse returned NULL and no active_from is configured, so no release "
            "can be attributed.",
        ]
    if result.status is Status.ON_TIME:
        return _on_time_reasoning(result, timezone)
    if result.release is None:
        return ["  1. No release has occurred yet."]

    # NOT_DUE and OVERDUE: `result.release` is the first release after the observation
    # (or at/after the `active_from` floor when the observation is NULL), never one
    # before it, and every line this function writes must name that same release.
    where = (
        "the observed timestamp"
        if result.observation is not None
        else "active_from (the floor is inclusive)"
    )
    lines = [f"  1. First release after {where}: {fmt(result.release.instant)}."]
    lines.append(
        "  2. It has occurred (release <= now)."
        if result.release.instant <= now
        else "  2. It has not occurred yet (release > now)."
    )
    deadline = result.deadline or result.release.instant
    if result.status is Status.NOT_DUE:
        lines.append(f"  3. Its deadline {fmt(deadline)} has not passed (now <= deadline).")
    else:
        lines.append(f"  3. Its deadline {fmt(deadline)} has passed (now > deadline).")
    return lines


def _on_time_reasoning(result: EvaluationResult, timezone: ZoneInfo) -> list[str]:
    """ON_TIME's own steps: the arrived release, then why nothing is due.

    The reported release is the latest release at or before ``now``; on ON_TIME it lies at
    or before the observation (otherwise it would be unarrived) and therefore arrived, and
    the first release after the observation is the next expected arrival, which is later
    than ``now`` — that is exactly why no release can be missing.
    """

    def fmt(value: datetime) -> str:
        return format_local(value, timezone)

    observation = result.observation
    if result.release is None or observation is None:
        return ["  1. No release has occurred yet."]
    lines = [
        f"  1. The latest release at or before the observed timestamp: "
        f"{fmt(result.release.instant)} arrived (observed {fmt(observation.instant)})."
    ]
    if result.next_expected_arrival is not None:
        lines.append(
            f"  2. The first release after the observed timestamp, "
            f"{fmt(result.next_expected_arrival)}, is later than now (release > now)."
        )
    else:
        lines.append(
            "  2. No release occurs after the observed timestamp within the search horizon."
        )
    lines.append("  3. Nothing due is outstanding: no release in the searched interval is missing.")
    return lines
