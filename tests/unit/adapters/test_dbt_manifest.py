"""Tests for the dbt manifest catalog (BLUEPRINT.md §4.8, §9.5)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from freshcal.adapters.dbt_manifest import DbtManifestCatalog
from freshcal.config.loader import Defaults, load_config
from freshcal.core.errors import ConfigError
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    HolidayCalendarRef,
    MonthlyBusinessDaySchedule,
    Origin,
)

FIXTURE_MANIFEST = Path("tests/fixtures/dbt_manifest_v12.json")
FIXTURE_CONFIG = Path("tests/fixtures/configs/integration/ecb_smoke.yml")


def catalog_for(path: Path = FIXTURE_MANIFEST, **kwargs: Any) -> DbtManifestCatalog:
    named = kwargs.pop("named_calendars", {})
    defaults = kwargs.pop("defaults", Defaults())
    return DbtManifestCatalog(path, defaults, named)


def entries_by_id(path: Path = FIXTURE_MANIFEST, **kwargs: Any) -> dict[str, Any]:
    return {entry.source_id: entry for entry in catalog_for(path, **kwargs).entries()}


def write_manifest(tmp_path: Path, document: Mapping[str, Any]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def manifest_with(sources: Mapping[str, Any], *, version: str | None = "v12") -> dict[str, Any]:
    metadata: dict[str, Any] = {"dbt_version": "1.12.5", "project_name": "fixture"}
    if version is not None:
        metadata["dbt_schema_version"] = f"https://schemas.getdbt.com/dbt/manifest/{version}.json"
    return {"metadata": metadata, "sources": dict(sources)}


def node(
    source_name: str,
    name: str,
    *,
    relation_name: str | None = None,
    loaded_at_field: str | None = "_loaded_at",
    loaded_at_query: str | None = None,
    filter_text: str | None = None,
    meta: Mapping[str, Any] | None = None,
    config_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "source_name": source_name,
        "name": name,
        "relation_name": relation_name or f'"fixture"."raw_{source_name}"."{name}"',
        "loaded_at_field": loaded_at_field,
        "loaded_at_query": loaded_at_query,
        "freshness": {"filter": filter_text},
    }
    if meta is not None:
        entry["meta"] = dict(meta)
    if config_meta is not None:
        entry["config"] = {"meta": dict(config_meta)}
    return entry


ECB_RULE = {
    "schedule": {"kind": "business_days", "time": "16:00", "timezone": "Europe/Berlin"},
    "grace": "2h",
}


def test_u_dbt_01_fixture_is_accepted_and_ecb_maps_per_specification() -> None:
    config = load_config(FIXTURE_CONFIG)
    entries = entries_by_id(defaults=config.defaults, named_calendars=config.named_calendars)
    assert sorted(entries) == [
        "ecb.fx_rates",
        "ecb.legacy_meta",
        "misc.forbidden_relation",
        "misc.query_based",
        "misc.unquoted_time",
        "stats.inherits",
        "stats.overrides",
    ]

    entry = entries["ecb.fx_rates"]
    assert entry.origin is Origin.DBT_MANIFEST
    assert entry.errors == ()
    assert entry.location == "dbt:source.freshcal_fixture.ecb.fx_rates"
    rule = entry.rule
    assert rule is not None
    assert rule.target.relation == '"fixture"."raw_ecb"."fx_rates"'
    assert rule.target.loaded_at_field == "_loaded_at"
    assert rule.target.filter is None
    assert rule.schedule == BusinessDaysSchedule(at=time(16, 0), timezone=ZoneInfo("Europe/Berlin"))
    assert rule.grace == timedelta(hours=2)
    assert rule.observed_timezone == ZoneInfo("UTC")
    # `calendar: target` is resolved against the config file's named calendars.
    assert rule.calendar == CalendarSpec(
        holiday_calendars=(HolidayCalendarRef("financial", "XECB"),), name="target"
    )


def test_u_dbt_01_named_calendars_and_defaults_come_from_the_config_file() -> None:
    config = load_config(FIXTURE_CONFIG)
    entries = entries_by_id(defaults=config.defaults, named_calendars=config.named_calendars)
    rule = entries["ecb.fx_rates"].rule
    assert rule is not None
    assert rule.calendar.name == "target"
    assert rule.calendar.holiday_calendars == (HolidayCalendarRef("financial", "XECB"),)


def test_u_dbt_01_freshness_filter_is_used_when_meta_has_none(tmp_path: Path) -> None:
    document = manifest_with(
        {
            "source.p.ecb.fx": node(
                "ecb",
                "fx",
                filter_text="_loaded_at > '2020-01-01'",
                config_meta={"freshcal": ECB_RULE},
            )
        }
    )
    entries = entries_by_id(write_manifest(tmp_path, document))
    rule = entries["ecb.fx"].rule
    assert rule is not None
    assert rule.target.filter == "_loaded_at > '2020-01-01'"

    # `meta.freshcal.filter` wins over the freshness block.
    document["sources"]["source.p.ecb.fx"]["meta"] = {
        "freshcal": {**ECB_RULE, "filter": "kind = 'real'"}
    }
    entries = entries_by_id(write_manifest(tmp_path, document))
    rule = entries["ecb.fx"].rule
    assert rule is not None
    assert rule.target.filter == "kind = 'real'"


@pytest.mark.parametrize(
    "version",
    ["v11", "v13", None],
    ids=["declares-v11", "declares-v13", "no-version"],
)
def test_u_dbt_02_unsupported_manifest_version_is_e301(tmp_path: Path, version: str | None) -> None:
    document = manifest_with({}, version=version)
    path = write_manifest(tmp_path, document)
    with pytest.raises(ConfigError) as excinfo:
        catalog_for(path).entries()
    issue = excinfo.value.issue
    assert issue.code == "E301"
    found = f"https://schemas.getdbt.com/dbt/manifest/{version}.json" if version else "missing"
    assert issue.message == (
        f"{path}: unsupported dbt manifest schema '{found}'; FreshCal 0.1 reads manifest "
        "schema v12 only (tested with dbt-core 1.12.5)"
    )


def test_u_dbt_03_missing_file_is_e302(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        catalog_for(tmp_path / "nope.json").entries()
    issue = excinfo.value.issue
    assert issue.code == "E302"
    assert issue.message.startswith(f"{tmp_path / 'nope.json'}: cannot read dbt manifest: ")


def test_u_dbt_03_invalid_json_is_e302(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        catalog_for(path).entries()
    issue = excinfo.value.issue
    assert issue.code == "E302"
    assert issue.message.startswith(f"{path}: cannot read dbt manifest: ")


def test_u_dbt_03_document_without_sources_is_e302(tmp_path: Path) -> None:
    path = write_manifest(
        tmp_path,
        {"metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"}},
    )
    with pytest.raises(ConfigError) as excinfo:
        catalog_for(path).entries()
    assert excinfo.value.issue.code == "E302"


def test_u_dbt_04_rule_under_config_meta_is_read(tmp_path: Path) -> None:
    document = manifest_with(
        {"source.p.ecb.fx": node("ecb", "fx", config_meta={"freshcal": ECB_RULE})}
    )
    entries = entries_by_id(write_manifest(tmp_path, document))
    assert entries["ecb.fx"].rule is not None


def test_u_dbt_05_rule_under_top_level_meta_is_read(tmp_path: Path) -> None:
    document = manifest_with(
        {"source.p.ecb.legacy": node("ecb", "legacy", meta={"freshcal": ECB_RULE})}
    )
    entries = entries_by_id(write_manifest(tmp_path, document))
    assert entries["ecb.legacy"].rule is not None


def test_u_dbt_06_inheritance_and_shallow_replacement() -> None:
    entries = entries_by_id()

    inherited = entries["stats.inherits"].rule
    assert inherited is not None
    assert inherited.schedule == MonthlyBusinessDaySchedule(
        business_day=3, at=time(9, 0), timezone=ZoneInfo("Europe/Berlin")
    )
    assert inherited.grace == timedelta(hours=4)
    assert inherited.calendar.holiday_calendars == (
        HolidayCalendarRef("country", "DE", subdivision="BY"),
    )

    # The table-level rule replaces the source-level one entirely: no calendar, US zone.
    replaced = entries["stats.overrides"].rule
    assert replaced is not None
    assert replaced.schedule == MonthlyBusinessDaySchedule(
        business_day=-1, at=time(17, 0), timezone=ZoneInfo("America/New_York")
    )
    assert replaced.grace == timedelta(hours=3)
    assert replaced.calendar.holiday_calendars == ()


def test_u_dbt_07_unquoted_time_is_e103_with_the_quote_hint() -> None:
    entry = entries_by_id()["misc.unquoted_time"]
    assert entry.rule is None
    assert [issue.code for issue in entry.errors] == ["E103"]
    assert entry.errors[0].message == (
        "dbt:source.freshcal_fixture.misc.unquoted_time:meta.freshcal.schedule.time: expected a "
        'quoted time such as "16:00", got the integer 960; YAML 1.1 reads unquoted 16:00 as the '
        'base-60 number 960, so write time: "16:00"'
    )


def test_u_dbt_08_loaded_at_query_without_loaded_at_field_is_e303() -> None:
    entry = entries_by_id()["misc.query_based"]
    assert entry.rule is None
    assert [issue.code for issue in entry.errors] == ["E303"]
    assert entry.errors[0].message == (
        "dbt:source.freshcal_fixture.misc.query_based: no loaded_at_field; set loaded_at_field on "
        "the dbt source or in meta.freshcal (loaded_at_query is not supported in v0.1)"
    )


def test_u_dbt_09_relation_inside_meta_freshcal_is_e304() -> None:
    entry = entries_by_id()["misc.forbidden_relation"]
    assert entry.rule is None
    assert [issue.code for issue in entry.errors] == ["E304"]
    assert entry.errors[0].message == (
        "dbt:source.freshcal_fixture.misc.forbidden_relation:meta.freshcal: 'relation' cannot be "
        "set in meta.freshcal because dbt provides it"
    )


def test_u_dbt_09_name_inside_meta_freshcal_is_e304(tmp_path: Path) -> None:
    document = manifest_with(
        {"source.p.ecb.fx": node("ecb", "fx", config_meta={"freshcal": {**ECB_RULE, "name": "x"}})}
    )
    entry = entries_by_id(write_manifest(tmp_path, document))["ecb.fx"]
    assert [issue.code for issue in entry.errors] == ["E304"]
    assert "'name' cannot be set in meta.freshcal" in entry.errors[0].message


def test_u_dbt_10_nodes_without_rules_are_ignored(tmp_path: Path) -> None:
    assert "misc.no_rules" not in entries_by_id()
    document = manifest_with(
        {
            "source.p.ecb.fx": node("ecb", "fx", config_meta={"freshcal": ECB_RULE}),
            "source.p.misc.plain": node("misc", "plain"),
        }
    )
    entries = entries_by_id(write_manifest(tmp_path, document))
    assert sorted(entries) == ["ecb.fx"]


def test_unknown_named_calendar_in_a_manifest_rule_is_e207(tmp_path: Path) -> None:
    document = manifest_with(
        {
            "source.p.ecb.fx": node(
                "ecb",
                "fx",
                config_meta={"freshcal": {**ECB_RULE, "calendar": "missing"}},
            )
        }
    )
    entry = entries_by_id(write_manifest(tmp_path, document))["ecb.fx"]
    assert [issue.code for issue in entry.errors] == ["E207"]
    assert entry.errors[0].message == (
        "dbt:source.p.ecb.fx.calendar: unknown calendar 'missing'; defined calendars: none"
    )


def test_w006_is_attached_to_a_manifest_source(tmp_path: Path) -> None:
    document = manifest_with(
        {
            "source.p.ecb.fx": node(
                "ecb",
                "fx",
                config_meta={
                    "freshcal": {
                        **ECB_RULE,
                        "calendar": {"overrides": [{"non_working_days": ["2026-12-24"]}]},
                    }
                },
            )
        }
    )
    entry = entries_by_id(write_manifest(tmp_path, document))["ecb.fx"]
    assert entry.rule is not None
    assert [issue.code for issue in entry.warnings] == ["W006"]
    assert entry.warnings[0].location == "ecb.fx"
