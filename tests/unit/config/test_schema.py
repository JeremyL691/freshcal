"""Tests for the configuration JSON Schema and its error mapping (BLUEPRINT.md §4.4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from freshcal.config.schema import (
    validate_dbt_rule,
    validate_document,
    validate_override_file,
    validate_source,
)
from freshcal.config.yaml_loader import load_yaml_file
from freshcal.core.errors import Issue

EXAMPLES_DIR = Path("tests/fixtures/configs/examples")
BLUEPRINT = Path("BLUEPRINT.md")

VALID_SOURCE: dict[str, Any] = {
    "name": "ecb.fx_rates",
    "relation": "raw.ecb_fx_rates",
    "loaded_at_field": "_loaded_at",
    "schedule": {"kind": "business_days", "time": "16:00", "timezone": "Europe/Berlin"},
    "grace": "2h",
}


def source(**overrides: Any) -> dict[str, Any]:
    return {**VALID_SOURCE, **overrides}


def test_u_schema_01_every_example_validates() -> None:
    paths = sorted(EXAMPLES_DIR.glob("*.yml"))
    assert [path.name for path in paths] == [
        "chinese_makeup.yml",
        "custom_overrides.yml",
        "ecb.yml",
        "last_business_day.yml",
        "multiple_calendars.yml",
        "nth_business_day.yml",
        "plain_cron.yml",
    ]
    for path in paths:
        doc = load_yaml_file(path)
        assert isinstance(doc, dict), path
        assert validate_document(doc) == [], path
        for index, entry in enumerate(doc.get("sources", [])):
            assert validate_source(entry, index) == [], f"{path} sources[{index}]"


@pytest.mark.parametrize(
    ("document", "index", "expected"),
    [
        pytest.param(
            source(typo=1),
            0,
            Issue("E101", "sources[0]: unknown field 'typo'", "sources[0]"),
            id="E101-unknown-field",
        ),
        pytest.param(
            {key: value for key, value in VALID_SOURCE.items() if key != "relation"},
            0,
            Issue("E102", "sources[0]: missing required field 'relation'", "sources[0]"),
            id="E102-missing-required",
        ),
        pytest.param(
            source(relation=5),
            0,
            Issue("E103", "sources[0].relation: expected string, got int", "sources[0].relation"),
            id="E103-generic-type",
        ),
        pytest.param(
            source(schedule={"kind": "business_days", "time": 960}),
            0,
            Issue(
                "E103",
                'sources[0].schedule.time: expected a quoted time such as "16:00", got the '
                "integer 960; YAML 1.1 reads unquoted 16:00 as the base-60 number 960, so "
                'write time: "16:00"',
                "sources[0].schedule.time",
            ),
            id="E103-unquoted-time",
        ),
        pytest.param(
            source(schedule={"kind": "hourly", "cron": "0 6 * * *"}),
            0,
            Issue(
                "E104",
                "sources[0].schedule.kind: 'hourly' is not one of: cron, business_days, "
                "monthly_business_day",
                "sources[0].schedule.kind",
            ),
            id="E104-value-not-allowed",
        ),
        pytest.param(
            source(schedule={"kind": "business_days", "time": "25:00"}),
            0,
            Issue(
                "E106",
                "sources[0].schedule.time: invalid value '25:00': expected \"HH:MM\" "
                '(24-hour), e.g. "16:00"',
                "sources[0].schedule.time",
            ),
            id="E106-time-format",
        ),
        pytest.param(
            source(active_from="2026-1-4"),
            0,
            Issue(
                "E106",
                "sources[0].active_from: invalid value '2026-1-4': expected \"YYYY-MM-DD\"",
                "sources[0].active_from",
            ),
            id="E106-date-format",
        ),
        pytest.param(
            source(grace="2x"),
            0,
            Issue(
                "E106",
                "sources[0].grace: invalid value '2x': expected a duration such as "
                '"90m", "2h", "1d6h"',
                "sources[0].grace",
            ),
            id="E106-duration-format",
        ),
        pytest.param(
            source(name="1bad"),
            0,
            Issue(
                "E106",
                "sources[0].name: invalid value '1bad': expected \"source\" or "
                '"source.table" using letters, digits, underscores',
                "sources[0].name",
            ),
            id="E106-source-name",
        ),
        pytest.param(
            source(schedule={"kind": "monthly_business_day", "time": "09:00", "business_day": 0}),
            0,
            Issue(
                "E204",
                "sources[0].schedule.business_day: business_day must be an integer between "
                "-23 and 23, excluding 0; got 0",
                "sources[0].schedule.business_day",
            ),
            id="E204-business-day-zero",
        ),
        pytest.param(
            source(schedule={"kind": "monthly_business_day", "time": "09:00", "business_day": 24}),
            0,
            Issue(
                "E204",
                "sources[0].schedule.business_day: business_day must be an integer between "
                "-23 and 23, excluding 0; got 24",
                "sources[0].schedule.business_day",
            ),
            id="E204-business-day-too-large",
        ),
        pytest.param(
            source(
                schedule={
                    "kind": "business_days",
                    "time": "16:00",
                    "on_non_business_day": "skip",
                }
            ),
            0,
            Issue(
                "E205",
                "sources[0].schedule: on_non_business_day is only valid for kind: cron",
                "sources[0].schedule",
            ),
            id="E205-policy-on-wrong-kind",
        ),
        pytest.param(
            source(calendar={"weekend": ["mon", "tue", "wed", "thu", "fri", "sat", "mon"]}),
            0,
            Issue(
                "E406",
                "sources[0].calendar.weekend: weekend may contain at most 6 days",
                "sources[0].calendar.weekend",
            ),
            id="E406-weekend-too-large",
        ),
    ],
)
def test_u_schema_02_source_errors(document: dict[str, Any], index: int, expected: Issue) -> None:
    assert validate_source(document, index) == [expected]


def test_u_schema_02_document_errors() -> None:
    assert validate_document({"version": 2}) == [
        Issue("E104", "version: unsupported config version; expected 1", "version")
    ]
    assert validate_document({"version": 1, "extra": True}) == [
        Issue("E101", "unknown field 'extra'", "")
    ]
    assert validate_document({}) == [Issue("E102", "missing required field 'version'", "")]
    assert validate_document({"version": 1, "sources": "nope"}) == [
        Issue("E103", "sources: expected array, got str", "sources")
    ]


@pytest.mark.parametrize("field", ["dsn", "password", "url", "uri", "conninfo", "user"])
def test_u_schema_02_secret_fields_are_e105(field: str) -> None:
    document = {"version": 1, "connection": {"type": "postgres", field: "value"}}
    assert validate_document(document) == [
        Issue(
            "E105",
            f"connection: '{field}' is not allowed; secrets must not be stored in config "
            "files. Put the DSN in an environment variable and set dsn_env to its name",
            "connection",
        )
    ]


def test_u_schema_02_dsn_env_format_is_e106() -> None:
    document = {"version": 1, "connection": {"type": "postgres", "dsn_env": "bad-name"}}
    assert validate_document(document) == [
        Issue(
            "E106",
            "connection.dsn_env: invalid value 'bad-name': expected an environment "
            "variable name such as FRESHCAL_PG_DSN",
            "connection.dsn_env",
        )
    ]


def test_u_schema_02_dbt_rule_forbids_name_and_relation() -> None:
    location = "dbt:source.freshcal_fixture.ecb.fx_rates:meta.freshcal"
    rule = {"schedule": {"kind": "business_days", "time": "16:00"}}
    assert validate_dbt_rule({**rule, "relation": "r"}, location) == [
        Issue(
            "E304",
            f"{location}: 'relation' cannot be set in meta.freshcal because dbt provides it",
            location,
        )
    ]
    assert validate_dbt_rule({**rule, "name": "x"}, location) == [
        Issue(
            "E304",
            f"{location}: 'name' cannot be set in meta.freshcal because dbt provides it",
            location,
        )
    ]
    assert validate_dbt_rule({**rule, "typo": 1}, location) == [
        Issue("E101", f"{location}: unknown field 'typo'", location)
    ]


def test_u_schema_04_source_errors_are_not_reported_by_validate_document() -> None:
    document = {
        "version": 1,
        "sources": [VALID_SOURCE, {"name": "x", "relation": "r", "loaded_at_field": "y"}],
    }
    assert validate_document(document) == []
    assert validate_source(document["sources"][1], 1) == [
        Issue("E102", "sources[1]: missing required field 'schedule'", "sources[1]")
    ]


def test_u_schema_03_test_schema_matches_blueprint() -> None:
    text = BLUEPRINT.read_text(encoding="utf-8")
    start = text.index("### 4.4 JSON Schema")
    block = text.index("```json", start)
    end = text.index("```", block + len("```json"))
    blueprint_schema = json.loads(text[block + len("```json") : end])

    committed = json.loads(
        Path("src/freshcal/schemas/config.v1.schema.json").read_text(encoding="utf-8")
    )
    assert committed == blueprint_schema


def test_validate_override_file_accepts_the_shipped_override_files() -> None:
    for path in sorted((EXAMPLES_DIR / "calendars").glob("*.yml")):
        assert validate_override_file(load_yaml_file(path), str(path)) == [], path
