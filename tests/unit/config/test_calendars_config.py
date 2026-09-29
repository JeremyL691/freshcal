"""Tests for calendar configuration (BLUEPRINT.md §4.3-§4.5, §3.3)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from freshcal.config.loader import (
    calendar_label,
    calendar_warnings,
    parse_calendar,
    parse_named_calendars,
    parse_weekend,
)
from freshcal.config.yaml_loader import load_yaml_file
from freshcal.core.errors import ConfigError
from freshcal.core.model import CalendarSpec, HolidayCalendarRef, Weekday

CALENDAR_FIXTURES = Path("tests/fixtures/configs/calendars")
EXAMPLES = Path("tests/fixtures/configs/examples")


def named_calendars(path: Path) -> dict[str, CalendarSpec]:
    doc = load_yaml_file(path)
    assert isinstance(doc, dict)
    return parse_named_calendars(doc, "", path.parent)


def source_calendar(path: Path, index: int = 0) -> CalendarSpec:
    doc = load_yaml_file(path)
    assert isinstance(doc, dict)
    named = parse_named_calendars(doc, "", path.parent)
    sources = doc["sources"]
    assert isinstance(sources, list)
    entry = sources[index]
    assert isinstance(entry, dict)
    return parse_calendar(entry.get("calendar"), named, f"sources[{index}].calendar", path.parent)


def error_of(call: object) -> ConfigError:
    with pytest.raises(ConfigError) as excinfo:
        call()  # type: ignore[operator]
    return excinfo.value


def test_u_ccal_01_default_calendar_when_none_given() -> None:
    spec = parse_calendar(None, {}, "sources[0].calendar", Path("."))
    assert spec == CalendarSpec()
    assert spec.weekend == frozenset({Weekday.SAT, Weekday.SUN})
    assert spec.holiday_calendars == ()
    assert spec.extra_working_days == frozenset()
    assert spec.extra_non_working_days == frozenset()
    assert spec.valid_until is None
    assert spec.name is None


def test_u_ccal_02_weekend_parsing() -> None:
    assert parse_weekend(None, "loc") == frozenset({Weekday.SAT, Weekday.SUN})
    assert parse_weekend([], "loc") == frozenset()
    assert parse_weekend(["mon", "sun"], "loc") == frozenset({Weekday.MON, Weekday.SUN})
    spec = parse_calendar({"weekend": ["fri"]}, {}, "sources[0].calendar", Path("."))
    assert spec.weekend == frozenset({Weekday.FRI})


def test_u_ccal_03_named_reference_and_unknown_name() -> None:
    spec = source_calendar(CALENDAR_FIXTURES / "company-calendar.yml")
    assert spec.name == "company"
    assert spec.valid_until == date(2026, 12, 31)
    assert spec.holiday_calendars == (HolidayCalendarRef("country", "DE"),)

    error = error_of(
        lambda: parse_calendar("nope", {"company": spec}, "sources[0].calendar", Path("."))
    )
    assert error.issue.code == "E207"
    assert error.issue.message == (
        "sources[0].calendar: unknown calendar 'nope'; defined calendars: company"
    )

    error = error_of(lambda: parse_calendar("nope", {}, "sources[0].calendar", Path(".")))
    assert error.issue.message == (
        "sources[0].calendar: unknown calendar 'nope'; defined calendars: none"
    )


def test_u_ccal_04_unknown_country() -> None:
    error = error_of(
        lambda: parse_calendar(
            {"holidays": [{"country": "XX"}]}, {}, "sources[0].calendar", Path(".")
        )
    )
    assert error.issue.code == "E401"
    assert error.issue.message == "sources[0].calendar.holidays[0]: unknown country 'XX'"


def test_u_ccal_04_unknown_subdivision_lists_valid_values() -> None:
    error = error_of(
        lambda: parse_calendar(
            {"holidays": [{"country": "DE", "subdivision": "XX"}]},
            {},
            "sources[0].calendar",
            Path("."),
        )
    )
    assert error.issue.code == "E401"
    assert error.issue.message.startswith(
        "sources[0].calendar.holidays[0]: unknown subdivision 'XX' for DE; valid: "
    )
    assert "BY" in error.issue.message


def test_u_ccal_04_unknown_category_lists_valid_values() -> None:
    error = error_of(
        lambda: parse_calendar(
            {"holidays": [{"country": "CN", "categories": ["workday"]}]},
            {},
            "sources[0].calendar",
            Path("."),
        )
    )
    assert error.issue.code == "E401"
    assert error.issue.message.startswith(
        "sources[0].calendar.holidays[0]: unknown category 'workday' for CN; valid: "
    )
    assert "public" in error.issue.message


def test_u_ccal_04_unknown_financial_market() -> None:
    error = error_of(
        lambda: parse_calendar(
            {"holidays": [{"financial": "XFOO"}]}, {}, "sources[0].calendar", Path(".")
        )
    )
    assert error.issue.code == "E402"
    assert error.issue.message.startswith(
        "sources[0].calendar.holidays[0]: unknown financial market 'XFOO'; valid: "
    )
    assert "XECB" in error.issue.message


def test_u_ccal_05_inline_overrides() -> None:
    spec = parse_calendar(
        {
            "overrides": [
                {"working_days": ["2026-09-20"], "non_working_days": ["2026-06-12"]},
                {"working_days": ["2026-10-10"]},
            ]
        },
        {},
        "sources[0].calendar",
        Path("."),
    )
    assert spec.extra_working_days == frozenset({date(2026, 9, 20), date(2026, 10, 10)})
    assert spec.extra_non_working_days == frozenset({date(2026, 6, 12)})


def test_u_ccal_06_override_file_is_relative_to_the_config_directory() -> None:
    spec = source_calendar(CALENDAR_FIXTURES / "company-calendar.yml")
    assert spec.extra_working_days == frozenset({date(2026, 1, 10), date(2026, 1, 11)})
    assert spec.extra_non_working_days == frozenset({date(2026, 3, 2)})


def test_u_ccal_07_missing_override_file(tmp_path: Path) -> None:
    config = tmp_path / "config.yml"
    config.write_text(
        "version: 1\ncalendars:\n  c:\n    overrides:\n      - file: gone.yml\n", encoding="utf-8"
    )
    error = error_of(lambda: source_calendar(config))
    assert error.issue.code == "E404"
    assert error.issue.message == (
        f"calendars.c.overrides[0]: override file '{tmp_path / 'gone.yml'}': file not found"
    )


def test_u_ccal_07_invalid_yaml_override_file(tmp_path: Path) -> None:
    (tmp_path / "broken.yml").write_text("working_days: [\n", encoding="utf-8")
    config = tmp_path / "config.yml"
    config.write_text(
        "version: 1\ncalendars:\n  c:\n    overrides:\n      - file: broken.yml\n", encoding="utf-8"
    )
    error = error_of(lambda: parse_named_calendars(load_yaml_file(config), "", tmp_path))
    assert error.issue.code == "E404"
    assert "YAML syntax error" in error.issue.message


def test_u_ccal_07_schema_error_in_override_file(tmp_path: Path) -> None:
    (tmp_path / "bad.yml").write_text("working_days: []\ntypo: 1\n", encoding="utf-8")
    config = tmp_path / "config.yml"
    config.write_text(
        "version: 1\ncalendars:\n  c:\n    overrides:\n      - file: bad.yml\n", encoding="utf-8"
    )
    error = error_of(lambda: parse_named_calendars(load_yaml_file(config), "", tmp_path))
    assert error.issue.code == "E404"
    assert error.issue.message.endswith("unknown field 'typo'")


def test_u_ccal_07_impossible_date_in_override_file(tmp_path: Path) -> None:
    (tmp_path / "bad.yml").write_text('working_days: ["2026-02-30"]\n', encoding="utf-8")
    config = tmp_path / "config.yml"
    config.write_text(
        "version: 1\ncalendars:\n  c:\n    overrides:\n      - file: bad.yml\n", encoding="utf-8"
    )
    error = error_of(lambda: parse_named_calendars(load_yaml_file(config), "", tmp_path))
    assert error.issue.code == "E404"
    assert error.issue.message.endswith("invalid value '2026-02-30': not a valid calendar date")


def test_u_ccal_08_date_conflict_is_e403() -> None:
    error = error_of(lambda: source_calendar(CALENDAR_FIXTURES / "conflict.yml"))
    assert error.issue.code == "E403"
    assert error.issue.message == (
        "calendars.conflicting: 2026-06-12 is listed as both a working day and a non-working day"
    )


def test_u_ccal_09_valid_until_is_parsed_and_validated() -> None:
    spec = parse_calendar({"valid_until": "2026-12-31"}, {}, "sources[0].calendar", Path("."))
    assert spec.valid_until == date(2026, 12, 31)

    error = error_of(
        lambda: parse_calendar({"valid_until": "2026-02-30"}, {}, "sources[0].calendar", Path("."))
    )
    assert error.issue.code == "E106"
    assert error.issue.message == (
        "sources[0].calendar.valid_until: invalid value '2026-02-30': not a valid calendar date"
    )


def test_u_ccal_10_w006_for_overrides_without_valid_until() -> None:
    plain = parse_calendar(
        {"overrides": [{"non_working_days": ["2026-12-24"]}]}, {}, "sources[0].calendar", Path(".")
    )
    warnings = calendar_warnings(plain, source_id="acme.timesheets", location="acme.timesheets")
    assert len(warnings) == 1
    assert warnings[0].code == "W006"
    assert warnings[0].location == "acme.timesheets"
    assert warnings[0].message == (
        "acme.timesheets: calendar of acme.timesheets has overrides but no valid_until; "
        "override dates are usually valid for one year, so set valid_until to the last date "
        "you checked"
    )

    assert (
        calendar_warnings(
            parse_calendar(
                {"valid_until": "2026-12-31", "overrides": [{"non_working_days": ["2026-12-24"]}]},
                {},
                "sources[0].calendar",
                Path("."),
            ),
            source_id="acme.timesheets",
            location="acme.timesheets",
        )
        == ()
    )
    # named calendars use their name as the label, and calendars without overrides are silent
    named = named_calendars(CALENDAR_FIXTURES / "company-calendar.yml")["company"]
    assert calendar_label(named) == "company"
    assert calendar_label(CalendarSpec(), "acme.timesheets") == "of acme.timesheets"
    assert calendar_warnings(CalendarSpec(), source_id="x") == ()


def test_acceptance_chinese_makeup_calendar() -> None:
    """T-1.4 acceptance: the §4.5 six make-up workdays and the CN holiday reference."""
    spec = source_calendar(EXAMPLES / "chinese_makeup.yml")
    assert spec.extra_working_days == frozenset(
        {
            date(2026, 1, 4),
            date(2026, 2, 14),
            date(2026, 2, 28),
            date(2026, 5, 9),
            date(2026, 9, 20),
            date(2026, 10, 10),
        }
    )
    assert spec.holiday_calendars == (HolidayCalendarRef("country", "CN"),)
    assert spec.valid_until == date(2026, 12, 31)
    assert calendar_warnings(spec, source_id="cn.daily_sales") == ()
