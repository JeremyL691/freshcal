"""Configuration loading: calendars (T-1.4), then sources and rules (T-1.5).

Everything a rule field can hold is resolved here: named and inline calendars,
holiday references, override files, and the load-time warnings that belong to a
calendar (``W006``). Structure is validated first (:mod:`freshcal.config.schema`),
so this module concentrates on semantics — time zones, dates, holiday codes,
override conflicts — and on producing exactly one coded :class:`Issue` per problem.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from croniter import CroniterBadCronError, CroniterBadDateError, croniter

from freshcal.adapters.holidays_provider import validate_ref
from freshcal.config.schema import validate_document, validate_override_file, validate_source
from freshcal.config.yaml_loader import load_yaml, load_yaml_file
from freshcal.core.errors import ConfigError, Issue, read_failure_reason, render_value, truncate
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    CronSchedule,
    FreshnessTarget,
    HolidayCalendarRef,
    MonthlyBusinessDaySchedule,
    NonBusinessDayPolicy,
    Origin,
    Schedule,
    SourceEntry,
    SourceRule,
    Weekday,
    mark_duplicate_source_ids,
)

__all__ = [
    "AppConfig",
    "Defaults",
    "DuckDBConnection",
    "PostgresConnection",
    "build_rule",
    "calendar_label",
    "calendar_warnings",
    "load_config",
    "parse_calendar",
    "parse_duration",
    "parse_named_calendars",
    "parse_timezone",
    "parse_weekend",
    "validate_cron",
]

_WEEKDAYS: dict[str, Weekday] = {
    "mon": Weekday.MON,
    "tue": Weekday.TUE,
    "wed": Weekday.WED,
    "thu": Weekday.THU,
    "fri": Weekday.FRI,
    "sat": Weekday.SAT,
    "sun": Weekday.SUN,
}
_ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _issue(code: str, location: str, text: str) -> Issue:
    return Issue(code, f"{location}: {text}" if location else text, location)


def _value_text(value: object) -> str:
    """The quoted form used inside messages that read ``invalid value '…'``."""
    if isinstance(value, str):
        return f"'{truncate(value)}'"
    return render_value(value)


@lru_cache(maxsize=1)
def _available_zones() -> frozenset[str]:
    """Every IANA zone name ``zoneinfo`` can serve, as a set for exact membership.

    ``available_timezones()`` enumerates the zone database once (the result is cached);
    membership is case-sensitive on every platform, unlike ``ZoneInfo``'s file lookup,
    which on macOS resolves ``europe/berlin`` through the case-insensitive file system
    (CFG-19).
    """
    return frozenset(available_timezones())


def parse_date(value: object, location: str) -> date:
    """Parse ``YYYY-MM-DD`` into a real calendar date, or raise ``E106``."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        raise ConfigError(
            _issue("E106", location, f'invalid value {_value_text(value)}: expected "YYYY-MM-DD"')
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ConfigError(
            _issue(
                "E106",
                location,
                f"invalid value {_value_text(value)}: not a valid calendar date",
            )
        ) from error


def parse_weekend(value: object, location: str) -> frozenset[Weekday]:
    """Convert a ``weekend`` list; ``None`` means the default Saturday and Sunday."""
    if value is None:
        return frozenset({Weekday.SAT, Weekday.SUN})
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ConfigError(_issue("E106", location, f"invalid value {_value_text(value)}"))
    weekdays: set[Weekday] = set()
    for entry in value:
        if not isinstance(entry, str) or entry not in _WEEKDAYS:
            raise ConfigError(_issue("E106", location, f"invalid value {_value_text(entry)}"))
        weekdays.add(_WEEKDAYS[entry])
    return frozenset(weekdays)


def _parse_holiday_ref(entry: object, location: str) -> HolidayCalendarRef:
    if not isinstance(entry, Mapping):
        raise ConfigError(_issue("E106", location, f"invalid value {_value_text(entry)}"))
    if "financial" in entry:
        ref = HolidayCalendarRef(kind="financial", code=str(entry["financial"]))
    else:
        categories = entry.get("categories")
        ref = HolidayCalendarRef(
            kind="country",
            code=str(entry["country"]),
            subdivision=str(entry["subdivision"]) if entry.get("subdivision") else None,
            categories=tuple(str(value) for value in categories) if categories else ("public",),
        )
    issue = validate_ref(ref, location)
    if issue is not None:
        raise ConfigError(issue)
    return ref


def _read_override_file(path: Path, location: str) -> object:
    """Read and validate an override file, mapping every failure to ``E404``.

    Every read failure (missing file, directory, permissions, not UTF-8) is E404 with a
    reason; a read error must never escape as E599 (CFG-09).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        reason = read_failure_reason(error)
        raise ConfigError(_issue("E404", location, f"override file '{path}': {reason}")) from error
    try:
        document = load_yaml(text, source=str(path))
    except ConfigError as error:
        detail = error.issue.message.partition(": ")[2] or error.issue.message
        raise ConfigError(_issue("E404", location, f"override file '{path}': {detail}")) from error
    issues = validate_override_file(document, "")
    if issues:
        raise ConfigError(_issue("E404", location, f"override file '{path}': {issues[0].message}"))
    return document


def _override_days(document: object, location: str) -> tuple[set[date], set[date]]:
    """Extract ``working_days`` and ``non_working_days`` from an override document."""
    if not isinstance(document, Mapping):
        raise ConfigError(_issue("E106", location, f"invalid value {_value_text(document)}"))
    working = {
        parse_date(value, f"{location}.working_days") for value in document.get("working_days", ())
    }
    non_working = {
        parse_date(value, f"{location}.non_working_days")
        for value in document.get("non_working_days", ())
    }
    return working, non_working


def _parse_overrides(
    entries: object, location: str, config_dir: Path
) -> tuple[frozenset[date], frozenset[date]]:
    if not isinstance(entries, Sequence) or isinstance(entries, str):
        raise ConfigError(_issue("E106", location, f"invalid value {_value_text(entries)}"))
    working: set[date] = set()
    non_working: set[date] = set()
    for index, entry in enumerate(entries):
        entry_location = f"{location}.overrides[{index}]"
        if not isinstance(entry, Mapping):
            raise ConfigError(_issue("E106", entry_location, f"invalid value {_value_text(entry)}"))
        if "file" in entry:
            path = config_dir / str(entry["file"])
            document = _read_override_file(path, entry_location)
            # Dates inside an override file are reported as E404 naming the file: the
            # schema cannot see that "2026-02-30" is not a real date.
            try:
                file_working, file_non_working = _override_days(document, "")
            except ConfigError as error:
                raise ConfigError(
                    _issue("E404", entry_location, f"override file '{path}': {error.issue.message}")
                ) from error
            working |= file_working
            non_working |= file_non_working
        else:
            entry_working, entry_non_working = _override_days(entry, entry_location)
            working |= entry_working
            non_working |= entry_non_working
    return frozenset(working), frozenset(non_working)


def _calendar_from_mapping(
    value: Mapping[str, object], location: str, config_dir: Path, *, name: str | None
) -> CalendarSpec:
    weekend = parse_weekend(value.get("weekend"), f"{location}.weekend")
    holiday_entries = value.get("holidays") or ()
    if not isinstance(holiday_entries, Sequence) or isinstance(holiday_entries, str):
        raise ConfigError(
            _issue("E106", f"{location}.holidays", f"invalid value {_value_text(holiday_entries)}")
        )
    holiday_calendars = tuple(
        _parse_holiday_ref(entry, f"{location}.holidays[{index}]")
        for index, entry in enumerate(holiday_entries)
    )
    working, non_working = _parse_overrides(value.get("overrides") or (), location, config_dir)
    conflict = sorted(working & non_working)
    if conflict:
        raise ConfigError(
            _issue(
                "E403",
                location,
                f"{conflict[0].isoformat()} is listed as both a working day and a non-working day",
            )
        )
    valid_until_value = value.get("valid_until")
    valid_until = (
        parse_date(valid_until_value, f"{location}.valid_until")
        if valid_until_value is not None
        else None
    )
    return CalendarSpec(
        weekend=weekend,
        holiday_calendars=holiday_calendars,
        extra_working_days=working,
        extra_non_working_days=non_working,
        valid_until=valid_until,
        name=name,
    )


def parse_named_calendars(
    doc: Mapping[str, object], loc: str, config_dir: Path
) -> dict[str, CalendarSpec]:
    """Parse the top-level ``calendars`` section into named :class:`CalendarSpec`.

    One calendar's problem does not hide another's: every calendar is attempted and all
    errors are raised together (CFG-04).
    """
    entries = doc.get("calendars") or {}
    if not isinstance(entries, Mapping):
        raise ConfigError(
            _issue("E106", f"{loc}.calendars" if loc else "calendars", "expected mapping")
        )
    named: dict[str, CalendarSpec] = {}
    issues: list[Issue] = []
    for name, value in entries.items():
        location = f"calendars.{name}" if not loc else f"{loc}.calendars.{name}"
        if not isinstance(value, Mapping):
            issues.append(_issue("E106", location, f"invalid value {_value_text(value)}"))
            continue
        try:
            named[str(name)] = _calendar_from_mapping(value, location, config_dir, name=str(name))
        except ConfigError as error:
            issues.extend(error.issues)
    if issues:
        raise ConfigError(issues)
    return named


def parse_calendar(
    value: object,
    named: Mapping[str, CalendarSpec],
    loc: str,
    config_dir: Path,
) -> CalendarSpec:
    """Resolve a source's ``calendar`` field: ``None``, a name, or inline settings."""
    if value is None:
        return CalendarSpec()
    if isinstance(value, str):
        if value not in named:
            defined = ", ".join(sorted(named)) if named else "none"
            raise ConfigError(
                _issue("E207", loc, f"unknown calendar '{value}'; defined calendars: {defined}")
            )
        return named[value]
    if isinstance(value, Mapping):
        return _calendar_from_mapping(value, loc, config_dir, name=None)
    raise ConfigError(_issue("E106", loc, f"invalid value {_value_text(value)}"))


def calendar_label(spec: CalendarSpec, source_id: str | None = None) -> str:
    """The label used in E408 and W006 messages (§4.6)."""
    if spec.name is not None:
        return spec.name
    if source_id is not None:
        return f"of {source_id}"
    return "inline"


def calendar_warnings(spec: CalendarSpec, *, source_id: str | None = None) -> tuple[Issue, ...]:
    """Load-time warnings for one calendar: ``W006`` when overrides have no expiry.

    Per §4.6 the ``W006`` template has no ``{loc}``: the calendar label already names the
    calendar (or ``of <source_id>`` for an inline one), so repeating the source id as a
    location would say it twice (CLI-03/CLI-17).
    """
    if spec.valid_until is None and (spec.extra_working_days or spec.extra_non_working_days):
        return (
            Issue(
                "W006",
                f"calendar {calendar_label(spec, source_id)} has overrides but no valid_until; "
                "override dates are usually valid for one year, so set valid_until to the "
                "last date you checked",
            ),
        )
    return ()


# --------------------------------------------------------------------------
# Rules, defaults, connection, and the top-level loader (T-1.5)
# --------------------------------------------------------------------------

_DURATION_RE = re.compile(r"^(?:([0-9]+)d)?(?:([0-9]+)h)?(?:([0-9]+)m)?$")
_CRON_EXTENSION_RE = re.compile(r"^[HR](\(.*\))?(/\d+)?$")
#: Fixed reference for the load-time cron check: an expression that cannot produce a
#: release within croniter's 50-year search from here is refused (SEM-08).
_CRON_VALIDATION_START = datetime(2026, 1, 1)
MAX_DURATION = timedelta(days=366)
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 30
DBS = "duckdb", "postgres"


@dataclass(frozen=True, slots=True)
class DuckDBConnection:
    """DuckDB connection settings; ``path`` is absolute or ``:memory:``."""

    path: str


@dataclass(frozen=True, slots=True)
class PostgresConnection:
    """PostgreSQL connection settings; the DSN itself never appears in config."""

    dsn_env: str | None = None
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class Defaults:
    """The ``defaults`` section, resolved: everything is optional."""

    timezone: ZoneInfo | None = None
    grace: timedelta | None = None
    calendar: CalendarSpec | None = None
    observed_timezone: ZoneInfo | None = None


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Everything ``load_config`` produces."""

    path: Path
    directory: Path
    connection: DuckDBConnection | PostgresConnection | None
    dbt_manifest: Path | None
    defaults: Defaults
    named_calendars: Mapping[str, CalendarSpec]
    entries: tuple[SourceEntry, ...]


def parse_duration(value: object, location: str) -> timedelta:
    """Parse ``30m``/``2h``/``1d6h`` (``d`` = 24 elapsed hours) or raise ``E203``/``E106``."""
    if not isinstance(value, str) or not _DURATION_RE.match(value) or value == "":
        raise ConfigError(
            _issue(
                "E106",
                location,
                f"invalid value {_value_text(value)}: expected a duration such as "
                '"90m", "2h", "1d6h"',
            )
        )
    match = _DURATION_RE.match(value)
    assert match is not None  # the pattern was checked above
    days, hours, minutes = (int(part) if part else 0 for part in match.groups())
    try:
        total = timedelta(days=days, hours=hours, minutes=minutes)
    except OverflowError as error:
        # ``timedelta`` cannot even represent the number (CFG-10): the duration is too
        # long, which is E203 — not an internal error.
        raise ConfigError(
            _issue("E203", location, f"duration '{truncate(value)}' exceeds the maximum of 366d")
        ) from error
    if total > MAX_DURATION:
        raise ConfigError(
            _issue("E203", location, f"duration '{truncate(value)}' exceeds the maximum of 366d")
        )
    return total


def parse_timezone(value: object, location: str) -> ZoneInfo:
    """Return the IANA zone or raise ``E201``.

    The name must be a member of ``zoneinfo.available_timezones()``: the membership test
    is case-sensitive everywhere, while ``ZoneInfo`` itself follows the OS (macOS
    resolves ``europe/berlin``; Linux does not), so a config that loads on one platform
    must load identically on the other (CFG-19).
    """
    if not isinstance(value, str) or not value or value not in _available_zones():
        text = f"'{truncate(value)}'" if isinstance(value, str) else f"'{render_value(value)}'"
        raise ConfigError(_issue("E201", location, f"unknown time zone {text}"))
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(
            _issue("E201", location, f"unknown time zone '{truncate(value)}'")
        ) from error


def validate_cron(expression: object, location: str) -> str:
    """Validate a 5-field cron expression and return it; failures are ``E202``."""
    if not isinstance(expression, str):
        raise ConfigError(
            _issue(
                "E202",
                location,
                f"invalid cron expression {render_value(expression)}: expected a string",
            )
        )

    def invalid(reason: str) -> ConfigError:
        return ConfigError(
            _issue(
                "E202",
                location,
                f"invalid cron expression '{truncate(expression)}': {reason}",
            )
        )

    fields = expression.split()
    if len(fields) != 5:
        raise invalid(f"expected 5 fields, got {len(fields)}")
    for field in fields:
        for element in field.split(","):
            if _CRON_EXTENSION_RE.match(element):
                raise invalid("hashed (H) and random (R) fields are not supported")
    try:
        # A fixed start keeps the check deterministic; `croniter(expression)` alone would
        # use the wall clock and would never notice an expression that cannot fire.
        croniter(expression, _CRON_VALIDATION_START).get_next(datetime)
    except CroniterBadDateError:
        # croniter refuses an expression it can find no next date for. Both cases stay
        # loadable: a never-firing expression such as `0 0 30 2 *` has no alternative
        # reading and evaluation reports E209 for it (CLI-03/CFG-12), and an expression
        # whose day-of-month *and* day-of-week fields are both restricted is the standard
        # OR case, which FreshCal computes itself from the two single-branch streams
        # (AUD-01, A-19) instead of refusing a schedule that really fires.
        pass
    except (CroniterBadCronError, ValueError) as error:
        raise invalid(str(error)) from error
    return expression


def _parse_time_of_day(value: object, location: str) -> time:
    if not isinstance(value, str):
        raise ConfigError(
            _issue(
                "E103",
                location,
                f'expected a quoted time such as "16:00", got {type(value).__name__}',
            )
        )
    try:
        return time.fromisoformat(value)
    except ValueError as error:
        raise ConfigError(
            _issue(
                "E106",
                location,
                f'invalid value {_value_text(value)}: expected "HH:MM" (24-hour), e.g. "16:00"',
            )
        ) from error


def _required(mapping: Mapping[str, object], field: str, location: str) -> object:
    value = mapping.get(field)
    if value is None:
        raise ConfigError(
            _issue(
                "E208", location, f"'{field}' is required; set it on the source or under defaults"
            )
        )
    return value


def _parse_schedule(value: object, defaults: Defaults, location: str) -> Schedule:
    if not isinstance(value, Mapping):
        raise ConfigError(_issue("E102", location, "missing required field 'schedule'"))
    kind = value.get("kind")
    timezone_value = value.get("timezone")
    timezone = (
        parse_timezone(timezone_value, f"{location}.timezone")
        if timezone_value is not None
        else defaults.timezone
    )
    if timezone is None:
        raise ConfigError(
            _issue(
                "E208",
                f"{location}.timezone",
                "'timezone' is required; set it on the source or under defaults",
            )
        )
    if kind == "cron":
        expression = validate_cron(_required(value, "cron", f"{location}.cron"), f"{location}.cron")
        policy_value = value.get("on_non_business_day")
        policy = (
            NonBusinessDayPolicy(str(policy_value))
            if policy_value is not None
            else NonBusinessDayPolicy.NONE
        )
        return CronSchedule(expression=expression, timezone=timezone, on_non_business_day=policy)
    if kind == "business_days":
        return BusinessDaysSchedule(
            at=_parse_time_of_day(_required(value, "time", f"{location}.time"), f"{location}.time"),
            timezone=timezone,
        )
    if kind == "monthly_business_day":
        business_day = _required(value, "business_day", f"{location}.business_day")
        if not isinstance(business_day, int) or isinstance(business_day, bool):
            raise ConfigError(
                _issue(
                    "E204",
                    f"{location}.business_day",
                    f"business_day must be an integer between -23 and 23, excluding 0; "
                    f"got {business_day}",
                )
            )
        return MonthlyBusinessDaySchedule(
            business_day=business_day,
            at=_parse_time_of_day(_required(value, "time", f"{location}.time"), f"{location}.time"),
            timezone=timezone,
        )
    raise ConfigError(
        _issue(
            "E104",
            f"{location}.kind",
            f"'{kind}' is not one of: cron, business_days, monthly_business_day",
        )
    )


def build_rule(
    mapping: Mapping[str, object],
    *,
    source_id: str,
    origin: Origin,
    relation: str,
    loaded_at_field: str,
    filter: str | None,
    defaults: Defaults,
    named_calendars: Mapping[str, CalendarSpec],
    location: str,
    config_dir: Path,
) -> SourceRule:
    """Resolve one source's rule: schedule, calendar, grace, zones, ``active_from``.

    Used for standalone sources and for ``meta.freshcal`` rules from a dbt manifest.
    """
    schedule = _parse_schedule(mapping.get("schedule"), defaults, f"{location}.schedule")
    calendar_value = mapping.get("calendar")
    if calendar_value is None:
        calendar = defaults.calendar if defaults.calendar is not None else CalendarSpec()
    else:
        calendar = parse_calendar(
            calendar_value, named_calendars, f"{location}.calendar", config_dir
        )

    grace_value = mapping.get("grace")
    if grace_value is not None:
        grace = parse_duration(grace_value, f"{location}.grace")
    elif defaults.grace is not None:
        grace = defaults.grace
    else:
        raise ConfigError(
            _issue(
                "E208",
                f"{location}.grace",
                "'grace' is required; set it on the source or under defaults",
            )
        )

    observed_timezone_value = mapping.get("observed_timezone")
    observed_timezone = (
        parse_timezone(observed_timezone_value, f"{location}.observed_timezone")
        if observed_timezone_value is not None
        else defaults.observed_timezone
    )

    active_from_value = mapping.get("active_from")
    active_from = (
        parse_date(active_from_value, f"{location}.active_from")
        if active_from_value is not None
        else None
    )

    return SourceRule(
        source_id=source_id,
        origin=origin,
        schedule=schedule,
        calendar=calendar,
        grace=grace,
        target=FreshnessTarget(relation=relation, loaded_at_field=loaded_at_field, filter=filter),
        observed_timezone=observed_timezone,
        active_from=active_from,
    )


def _parse_connection(
    value: object, directory: Path, location: str
) -> DuckDBConnection | PostgresConnection | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConfigError(_issue("E103", location, f"expected object, got {type(value).__name__}"))
    kind = value.get("type")
    if kind == "duckdb":
        path = str(_required(value, "path", f"{location}.path"))
        if path == ":memory:":
            return DuckDBConnection(path=path)
        candidate = Path(path)
        resolved = candidate if candidate.is_absolute() else directory / candidate
        return DuckDBConnection(path=str(resolved.resolve()))
    if kind == "postgres":
        dsn_env = value.get("dsn_env")
        timeout = value.get("statement_timeout_seconds")
        return PostgresConnection(
            dsn_env=str(dsn_env) if dsn_env is not None else None,
            statement_timeout_seconds=(
                int(timeout) if timeout is not None else DEFAULT_STATEMENT_TIMEOUT_SECONDS
            ),
        )
    raise ConfigError(
        _issue("E104", f"{location}.type", f"'{kind}' is not one of: duckdb, postgres")
    )


def _parse_defaults(value: object, named: Mapping[str, CalendarSpec], directory: Path) -> Defaults:
    """Parse the ``defaults`` section, collecting every field's error (CFG-04)."""
    if value is None:
        return Defaults()
    if not isinstance(value, Mapping):
        raise ConfigError(
            _issue("E103", "defaults", f"expected object, got {type(value).__name__}")
        )
    issues: list[Issue] = []
    timezone_value = value.get("timezone")
    grace_value = value.get("grace")
    calendar_value = value.get("calendar")
    observed_value = value.get("observed_timezone")

    timezone: ZoneInfo | None = None
    if timezone_value is not None:
        try:
            timezone = parse_timezone(timezone_value, "defaults.timezone")
        except ConfigError as error:
            issues.extend(error.issues)
    grace: timedelta | None = None
    if grace_value is not None:
        try:
            grace = parse_duration(grace_value, "defaults.grace")
        except ConfigError as error:
            issues.extend(error.issues)
    calendar: CalendarSpec | None = None
    if calendar_value is not None:
        try:
            calendar = parse_calendar(calendar_value, named, "defaults.calendar", directory)
        except ConfigError as error:
            issues.extend(error.issues)
    observed_timezone: ZoneInfo | None = None
    if observed_value is not None:
        try:
            observed_timezone = parse_timezone(observed_value, "defaults.observed_timezone")
        except ConfigError as error:
            issues.extend(error.issues)
    if issues:
        raise ConfigError(issues)
    return Defaults(
        timezone=timezone,
        grace=grace,
        calendar=calendar,
        observed_timezone=observed_timezone,
    )


def _source_id_of(entry: Mapping[str, object], index: int) -> str:
    """The source ID for one entry: its ``name`` when that is a string, else ``sources[index]``.

    The ID is built before the entry is validated, because its diagnostics need it, so the
    shape must be checked here rather than trusted: a YAML alias can expand a structured
    ``name`` into a huge object, and ``str()`` on it would build a multi-megabyte identifier
    and a multi-megabyte report (AUD-02). Only a string is used as an ID — never truncated,
    so valid identifiers are preserved exactly — and anything else is named by its position.
    """
    name = entry.get("name")
    if isinstance(name, str) and name:
        return name
    return f"sources[{index}]"


def _parse_source_entry(
    entry: object,
    index: int,
    *,
    defaults: Defaults,
    named: Mapping[str, CalendarSpec],
    directory: Path,
) -> SourceEntry:
    location = f"sources[{index}]"
    if not isinstance(entry, Mapping):
        return SourceEntry(
            source_id=location,
            origin=Origin.CONFIG,
            rule=None,
            errors=(_issue("E103", location, f"expected object, got {type(entry).__name__}"),),
            location=location,
        )
    source_id = _source_id_of(entry, index)
    issues = validate_source(entry, index)
    if issues:
        return SourceEntry(
            source_id=source_id,
            origin=Origin.CONFIG,
            rule=None,
            errors=tuple(issues),
            location=location,
        )
    try:
        rule = build_rule(
            entry,
            source_id=source_id,
            origin=Origin.CONFIG,
            relation=str(entry["relation"]),
            loaded_at_field=str(entry["loaded_at_field"]),
            filter=str(entry["filter"]) if entry.get("filter") is not None else None,
            defaults=defaults,
            named_calendars=named,
            location=location,
            config_dir=directory,
        )
    except ConfigError as error:
        return SourceEntry(
            source_id=source_id,
            origin=Origin.CONFIG,
            rule=None,
            errors=(error.issue,),
            location=location,
        )
    warnings = calendar_warnings(rule.calendar, source_id=source_id)
    return SourceEntry(
        source_id=source_id,
        origin=Origin.CONFIG,
        rule=rule,
        warnings=warnings,
        location=location,
    )


def _named_after(issues: Iterable[Issue], path: Path) -> list[Issue]:
    """Give document-level issues the config path as their location (CLI-19).

    An empty config file produced ``E103 expected object, got NoneType`` without naming
    the file; a document-level issue has no in-file location, so the path is the
    location. Issues that already point inside the document keep theirs.
    """
    return [
        Issue(issue.code, f"{path}: {issue.message}", str(path)) if not issue.location else issue
        for issue in issues
    ]


def load_config(path: Path) -> AppConfig:
    """Load a FreshCal config file into an :class:`AppConfig`.

    Top-level problems (YAML, unknown fields, ``connection``, ``defaults``,
    ``calendars``, ``dbt``) raise :class:`ConfigError` carrying **every** issue the
    document validator found and abort the command; problems attributable to one source
    mark that entry and leave the others usable.
    """
    document = load_yaml_file(path)
    if not isinstance(document, Mapping):
        raise ConfigError(
            _named_after(
                [_issue("E103", "", f"expected object, got {type(document).__name__}")], path
            )
        )
    top_level_issues = _named_after(validate_document(document), path)
    if top_level_issues:
        raise ConfigError(top_level_issues)

    directory = path.parent
    named = parse_named_calendars(document, "", directory)
    connection = _parse_connection(document.get("connection"), directory, "connection")
    dbt_section = document.get("dbt")
    dbt_manifest: Path | None = None
    if isinstance(dbt_section, Mapping) and dbt_section.get("manifest") is not None:
        candidate = Path(str(dbt_section["manifest"]))
        dbt_manifest = candidate if candidate.is_absolute() else (directory / candidate).resolve()
    defaults = _parse_defaults(document.get("defaults"), named, directory)

    raw_sources = document.get("sources") or []
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, str):
        raise ConfigError(
            _issue("E103", "sources", f"expected array, got {type(raw_sources).__name__}")
        )
    entries = [
        _parse_source_entry(entry, index, defaults=defaults, named=named, directory=directory)
        for index, entry in enumerate(raw_sources)
    ]

    return AppConfig(
        path=path,
        directory=directory,
        connection=connection,
        dbt_manifest=dbt_manifest,
        defaults=defaults,
        named_calendars=named,
        entries=tuple(mark_duplicate_source_ids(entries)),
    )
