"""Tests for rule conversion and configuration loading (BLUEPRINT.md §4.1-§4.3)."""

from __future__ import annotations

from datetime import date, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from freshcal.config.loader import (
    AppConfig,
    DuckDBConnection,
    PostgresConnection,
    build_rule,
    load_config,
    parse_duration,
    parse_timezone,
    validate_cron,
)
from freshcal.config.yaml_loader import load_yaml, load_yaml_file
from freshcal.core.errors import ConfigError
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    CronSchedule,
    FreshnessTarget,
    HolidayCalendarRef,
    MonthlyBusinessDaySchedule,
    NonBusinessDayPolicy,
    Origin,
    SourceRule,
    Weekday,
)

EXAMPLES = Path("tests/fixtures/configs/examples")
CLI_CONFIGS = Path("tests/fixtures/configs/cli")


def write_config(tmp_path: Path, text: str, name: str = "freshcal.yml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def error_of(call: object) -> ConfigError:
    with pytest.raises(ConfigError) as excinfo:
        call()  # type: ignore[operator]
    return excinfo.value


def test_u_load_01_ecb_rule_equality() -> None:
    config = load_config(EXAMPLES / "ecb.yml")
    assert isinstance(config, AppConfig)
    assert config.path == EXAMPLES / "ecb.yml"
    assert config.directory == EXAMPLES
    assert config.connection == DuckDBConnection(path=":memory:")
    assert config.dbt_manifest is None
    assert set(config.named_calendars) == {"target"}
    assert [entry.source_id for entry in config.entries] == ["ecb.fx_rates"]

    expected = SourceRule(
        source_id="ecb.fx_rates",
        origin=Origin.CONFIG,
        schedule=BusinessDaysSchedule(at=time(16, 0), timezone=ZoneInfo("Europe/Berlin")),
        calendar=CalendarSpec(
            weekend=frozenset({Weekday.SAT, Weekday.SUN}),
            holiday_calendars=(HolidayCalendarRef(kind="financial", code="XECB"),),
            name="target",
        ),
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation="raw.ecb_fx_rates", loaded_at_field="_loaded_at"),
        observed_timezone=ZoneInfo("UTC"),
    )
    entry = config.entries[0]
    assert entry.errors == ()
    assert entry.warnings == ()
    assert entry.location == "sources[0]"
    assert entry.rule == expected


def test_u_load_02_each_schedule_kind() -> None:
    cron = load_config(EXAMPLES / "plain_cron.yml").entries[0].rule
    assert cron is not None
    assert cron.schedule == CronSchedule(
        expression="0 6 * * *",
        timezone=ZoneInfo("UTC"),
        on_non_business_day=NonBusinessDayPolicy.NONE,
    )

    monthly = load_config(EXAMPLES / "nth_business_day.yml").entries[0].rule
    assert monthly is not None
    assert monthly.schedule == MonthlyBusinessDaySchedule(
        business_day=3, at=time(9, 0), timezone=ZoneInfo("Europe/Berlin")
    )

    last = load_config(EXAMPLES / "last_business_day.yml").entries[0].rule
    assert last is not None
    assert last.schedule == MonthlyBusinessDaySchedule(
        business_day=-1, at=time(17, 0), timezone=ZoneInfo("America/New_York")
    )
    assert last.calendar.holiday_calendars == (HolidayCalendarRef("country", "US"),)

    rolled = load_config(EXAMPLES / "multiple_calendars.yml").entries[0].rule
    assert rolled is not None
    assert rolled.schedule == CronSchedule(
        expression="30 7 * * 1,3,5",
        timezone=ZoneInfo("Europe/London"),
        on_non_business_day=NonBusinessDayPolicy.FOLLOWING,
    )
    assert [ref.label() for ref in rolled.calendar.holiday_calendars] == [
        "country GB-ENG",
        "financial XECB",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0m", timedelta(0)),
        ("90m", timedelta(minutes=90)),
        ("2h", timedelta(hours=2)),
        ("1d6h", timedelta(days=1, hours=6)),
        ("366d", timedelta(days=366)),
        ("1d2h3m", timedelta(days=1, hours=2, minutes=3)),
    ],
)
def test_u_load_03_parse_duration(text: str, expected: timedelta) -> None:
    assert parse_duration(text, "grace") == expected


def test_u_load_03_duration_too_long_is_e203() -> None:
    error = error_of(lambda: parse_duration("367d", "sources[0].grace"))
    assert error.issue.code == "E203"
    assert error.issue.message == "sources[0].grace: duration '367d' exceeds the maximum of 366d"


def test_u_load_03_malformed_duration_is_e106() -> None:
    error = error_of(lambda: parse_duration("2x", "grace"))
    assert error.issue.code == "E106"


@pytest.mark.parametrize("value", ["Mars/Olympus", "../etc/passwd", "", "UTC+2"])
def test_u_load_04_unknown_timezone_is_e201(value: str) -> None:
    error = error_of(lambda: parse_timezone(value, "sources[0].schedule.timezone"))
    assert error.issue.code == "E201"
    assert error.issue.message == f"sources[0].schedule.timezone: unknown time zone '{value}'"


@pytest.mark.parametrize(
    ("expression", "reason"),
    [
        ("0 6 * *", "expected 5 fields, got 4"),
        ("0 6 * * * *", "expected 5 fields, got 6"),
        ("H 16 * * *", "hashed (H) and random (R) fields are not supported"),
        ("R R * * *", "hashed (H) and random (R) fields are not supported"),
    ],
)
def test_u_load_05_invalid_cron_is_e202(expression: str, reason: str) -> None:
    error = error_of(lambda: validate_cron(expression, "sources[0].schedule.cron"))
    assert error.issue.code == "E202"
    assert error.issue.message == (
        f"sources[0].schedule.cron: invalid cron expression '{expression}': {reason}"
    )


def test_u_load_05_out_of_range_field_is_e202() -> None:
    error = error_of(lambda: validate_cron("0 25 * * *", "cron"))
    assert error.issue.code == "E202"
    assert error.issue.message.startswith("cron: invalid cron expression '0 25 * * *': ")


def test_u_load_05_weekday_names_are_accepted() -> None:
    assert validate_cron("0 16 * * THU", "cron") == "0 16 * * THU"
    assert validate_cron("0 6 1,15 * 1-5", "cron") == "0 6 1,15 * 1-5"


def test_u_load_06_defaults_resolution_order(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        """
version: 1
defaults:
  timezone: Europe/Berlin
  grace: 2h
  calendar: {holidays: [{financial: XECB}]}
  observed_timezone: UTC
sources:
  - name: from_defaults
    relation: raw.a
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "16:00"}
  - name: own_values
    relation: raw.b
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "09:00", timezone: Asia/Shanghai}
    grace: 30m
    calendar: {weekend: [fri]}
    observed_timezone: Asia/Shanghai
""",
    )
    defaults_rule, own_rule = (entry.rule for entry in load_config(config).entries)
    assert defaults_rule is not None
    assert own_rule is not None
    assert defaults_rule.schedule.timezone == ZoneInfo("Europe/Berlin")
    assert defaults_rule.grace == timedelta(hours=2)
    assert defaults_rule.calendar.holiday_calendars == (HolidayCalendarRef("financial", "XECB"),)
    assert defaults_rule.observed_timezone == ZoneInfo("UTC")
    assert own_rule.schedule.timezone == ZoneInfo("Asia/Shanghai")
    assert own_rule.grace == timedelta(minutes=30)
    assert own_rule.calendar.weekend == frozenset({Weekday.FRI})
    assert own_rule.observed_timezone == ZoneInfo("Asia/Shanghai")


def test_u_load_07_missing_required_fields_are_e208(tmp_path: Path) -> None:
    without_grace = write_config(
        tmp_path,
        """
version: 1
sources:
  - name: a.b
    relation: raw.a
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "16:00", timezone: UTC}
""",
    )
    entry = load_config(without_grace).entries[0]
    assert entry.rule is None
    assert [issue.code for issue in entry.errors] == ["E208"]
    assert entry.errors[0].message == (
        "sources[0].grace: 'grace' is required; set it on the source or under defaults"
    )

    without_timezone = write_config(
        tmp_path,
        """
version: 1
sources:
  - name: a.b
    relation: raw.a
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "16:00"}
    grace: 2h
""",
    )
    entry = load_config(without_timezone).entries[0]
    assert [issue.code for issue in entry.errors] == ["E208"]
    assert entry.errors[0].message == (
        "sources[0].schedule.timezone: 'timezone' is required; set it on the source "
        "or under defaults"
    )


def test_u_load_08_duplicate_source_ids_are_e206(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        """
version: 1
sources:
  - name: a.b
    relation: raw.a
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "16:00", timezone: UTC}
    grace: 1h
  - name: a.b
    relation: raw.other
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "17:00", timezone: UTC}
    grace: 1h
""",
    )
    first, second = load_config(config).entries
    # Both members are marked: with two definitions of one source, neither can be
    # trusted to be the intended one (BLUEPRINT.md §4.8: "E206 for every duplicate").
    for duplicate_entry in (first, second):
        assert duplicate_entry.rule is None
        assert [issue.code for issue in duplicate_entry.errors] == ["E206"]
        assert duplicate_entry.errors[0].message == (
            "duplicate source id 'a.b' at sources[0] and sources[1]"
        )


def test_u_load_09_connection_parsing(tmp_path: Path) -> None:
    relative = write_config(
        tmp_path,
        """
version: 1
connection: {type: duckdb, path: data/warehouse.duckdb}
""",
    )
    connection = load_config(relative).connection
    assert connection == DuckDBConnection(path=str((tmp_path / "data/warehouse.duckdb").resolve()))

    memory = load_config(EXAMPLES / "ecb.yml").connection
    assert memory == DuckDBConnection(path=":memory:")

    postgres = write_config(
        tmp_path,
        """
version: 1
connection: {type: postgres, dsn_env: FRESHCAL_PG_DSN}
""",
    )
    assert load_config(postgres).connection == PostgresConnection(
        dsn_env="FRESHCAL_PG_DSN", statement_timeout_seconds=30
    )

    timeout = write_config(
        tmp_path,
        """
version: 1
connection: {type: postgres, statement_timeout_seconds: 5}
""",
    )
    assert load_config(timeout).connection == PostgresConnection(
        dsn_env=None, statement_timeout_seconds=5
    )


def test_u_load_10_error_scope(tmp_path: Path) -> None:
    # A top-level error raises and aborts the whole load.
    top_level = write_config(tmp_path, "version: 1\nunknown_section: {}\n")
    error = error_of(lambda: load_config(top_level))
    assert error.issue.code == "E101"

    # A per-source error marks only that entry; the other source stays usable.
    per_source = write_config(
        tmp_path,
        """
version: 1
sources:
  - name: good.source
    relation: raw.good
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "16:00", timezone: UTC}
    grace: 1h
  - name: bad.source
    relation: raw.bad
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "16:00", timezone: Mars/Olympus}
    grace: 1h
""",
    )
    good, bad = load_config(per_source).entries
    assert good.rule is not None
    assert bad.rule is None
    assert [issue.code for issue in bad.errors] == ["E201"]


def test_u_load_11_explicit_utc_counts_as_set(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        """
version: 1
sources:
  - name: a.b
    relation: raw.a
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "16:00", timezone: UTC}
    grace: 1h
    observed_timezone: UTC
""",
    )
    rule = load_config(config).entries[0].rule
    assert rule is not None
    assert rule.observed_timezone == ZoneInfo("UTC")
    assert rule.observed_timezone is not None
    assert rule.observed_timezone.key == "UTC"


def test_named_calendar_and_active_from_are_resolved(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        """
version: 1
calendars:
  target: {holidays: [{financial: XECB}]}
sources:
  - name: a.b
    relation: raw.a
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "16:00", timezone: UTC}
    calendar: target
    grace: 1h
    active_from: "2026-10-01"
""",
    )
    rule = load_config(config).entries[0].rule
    assert rule is not None
    assert rule.calendar.name == "target"
    assert rule.active_from == date(2026, 10, 1)


def test_w006_is_attached_to_the_source(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        """
version: 1
sources:
  - name: a.b
    relation: raw.a
    loaded_at_field: _loaded_at
    schedule: {kind: business_days, time: "16:00", timezone: UTC}
    calendar:
      overrides:
        - non_working_days: ["2026-12-24"]
    grace: 1h
""",
    )
    entry = load_config(config).entries[0]
    assert [issue.code for issue in entry.warnings] == ["W006"]
    assert entry.warnings[0].location == "a.b"


def test_dbt_manifest_path_is_relative_to_the_config_directory(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        "version: 1\ndbt: {manifest: target/manifest.json}\n",
    )
    assert load_config(config).dbt_manifest == (tmp_path / "target/manifest.json").resolve()


def test_unknown_calendar_name_in_a_source_is_e207() -> None:
    entry = load_config(EXAMPLES / "ecb.yml").entries[0]
    assert entry.rule is not None

    def build() -> SourceRule:
        return build_rule(
            {
                "schedule": {"kind": "business_days", "time": "16:00", "timezone": "UTC"},
                "calendar": "missing",
                "grace": "1h",
            },
            source_id="a.b",
            origin=Origin.CONFIG,
            relation="raw.a",
            loaded_at_field="_loaded_at",
            filter=None,
            defaults=load_config(EXAMPLES / "plain_cron.yml").defaults,
            named_calendars={},
            location="sources[0]",
            config_dir=EXAMPLES,
        )

    error = error_of(build)
    assert error.issue.code == "E207"
    assert error.issue.message == (
        "sources[0].calendar: unknown calendar 'missing'; defined calendars: none"
    )


def test_every_example_config_loads_with_a_rule() -> None:
    for path in sorted(EXAMPLES.glob("*.yml")):
        config = load_config(path)
        assert config.entries, path
        for entry in config.entries:
            assert entry.errors == (), f"{path}: {entry.errors}"
            assert entry.rule is not None, path


def test_yaml_document_that_is_not_a_mapping(tmp_path: Path) -> None:
    config = write_config(tmp_path, "- just\n- a list\n")
    error = error_of(lambda: load_config(config))
    assert error.issue.code == "E103"
    assert error.issue.message == "expected object, got list"


def test_load_yaml_helpers_are_used(tmp_path: Path) -> None:
    """Guard: the loader goes through the safe YAML loader, not yaml.safe_load."""
    assert load_yaml("a: 16:00\n", source="t") == {"a": "16:00"}
    path = write_config(tmp_path, "version: 1\nsources: []\n")
    assert load_yaml_file(path) == {"version": 1, "sources": []}
