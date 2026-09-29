"""Configuration loading: calendars (T-1.4), then sources and rules (T-1.5).

Everything a rule field can hold is resolved here: named and inline calendars,
holiday references, override files, and the load-time warnings that belong to a
calendar (``W006``). Structure is validated first (:mod:`freshcal.config.schema`),
so this module concentrates on semantics — time zones, dates, holiday codes,
override conflicts — and on producing exactly one coded :class:`Issue` per problem.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from freshcal.adapters.holidays_provider import validate_ref
from freshcal.config.schema import validate_override_file
from freshcal.config.yaml_loader import load_yaml
from freshcal.core.errors import ConfigError, Issue
from freshcal.core.model import CalendarSpec, HolidayCalendarRef, Weekday

__all__ = [
    "calendar_label",
    "calendar_warnings",
    "parse_calendar",
    "parse_named_calendars",
    "parse_weekend",
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


def parse_date(value: object, location: str) -> date:
    """Parse ``YYYY-MM-DD`` into a real calendar date, or raise ``E106``."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        raise ConfigError(
            _issue("E106", location, f'invalid value {value!r}: expected "YYYY-MM-DD"')
        )
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ConfigError(
            _issue("E106", location, f"invalid value {value!r}: not a valid calendar date")
        ) from error


def parse_weekend(value: object, location: str) -> frozenset[Weekday]:
    """Convert a ``weekend`` list; ``None`` means the default Saturday and Sunday."""
    if value is None:
        return frozenset({Weekday.SAT, Weekday.SUN})
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ConfigError(_issue("E106", location, f"invalid value {value!r}"))
    weekdays: set[Weekday] = set()
    for entry in value:
        if not isinstance(entry, str) or entry not in _WEEKDAYS:
            raise ConfigError(_issue("E106", location, f"invalid value {entry!r}"))
        weekdays.add(_WEEKDAYS[entry])
    return frozenset(weekdays)


def _parse_holiday_ref(entry: object, location: str) -> HolidayCalendarRef:
    if not isinstance(entry, Mapping):
        raise ConfigError(_issue("E106", location, f"invalid value {entry!r}"))
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
    """Read and validate an override file, mapping every failure to ``E404``."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigError(
            _issue("E404", location, f"override file '{path}': file not found")
        ) from error
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
        raise ConfigError(_issue("E106", location, f"invalid value {document!r}"))
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
        raise ConfigError(_issue("E106", location, f"invalid value {entries!r}"))
    working: set[date] = set()
    non_working: set[date] = set()
    for index, entry in enumerate(entries):
        entry_location = f"{location}.overrides[{index}]"
        if not isinstance(entry, Mapping):
            raise ConfigError(_issue("E106", entry_location, f"invalid value {entry!r}"))
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
            _issue("E106", f"{location}.holidays", f"invalid value {holiday_entries!r}")
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
    """Parse the top-level ``calendars`` section into named :class:`CalendarSpec`."""
    entries = doc.get("calendars") or {}
    if not isinstance(entries, Mapping):
        raise ConfigError(
            _issue("E106", f"{loc}.calendars" if loc else "calendars", "expected mapping")
        )
    named: dict[str, CalendarSpec] = {}
    for name, value in entries.items():
        location = f"calendars.{name}" if not loc else f"{loc}.calendars.{name}"
        if not isinstance(value, Mapping):
            raise ConfigError(_issue("E106", location, f"invalid value {value!r}"))
        named[str(name)] = _calendar_from_mapping(value, location, config_dir, name=str(name))
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
    raise ConfigError(_issue("E106", loc, f"invalid value {value!r}"))


def calendar_label(spec: CalendarSpec, source_id: str | None = None) -> str:
    """The label used in E408 and W006 messages (§4.6)."""
    if spec.name is not None:
        return spec.name
    if source_id is not None:
        return f"of {source_id}"
    return "inline"


def calendar_warnings(
    spec: CalendarSpec, *, source_id: str | None = None, location: str = ""
) -> tuple[Issue, ...]:
    """Load-time warnings for one calendar: ``W006`` when overrides have no expiry."""
    if spec.valid_until is None and (spec.extra_working_days or spec.extra_non_working_days):
        return (
            _issue(
                "W006",
                location,
                f"calendar {calendar_label(spec, source_id)} has overrides but no valid_until; "
                "override dates are usually valid for one year, so set valid_until to the "
                "last date you checked",
            ),
        )
    return ()
