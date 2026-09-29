"""Tests for source merging and selection (BLUEPRINT.md §4.8, §6.2)."""

from __future__ import annotations

from datetime import time, timedelta
from zoneinfo import ZoneInfo

import pytest

from freshcal.app import merge_entries, select_entries
from freshcal.core.errors import ConfigError, Issue
from freshcal.core.model import (
    BusinessDaysSchedule,
    CalendarSpec,
    FreshnessTarget,
    Origin,
    SourceEntry,
    SourceRule,
)


def rule(source_id: str, *, origin: Origin = Origin.CONFIG) -> SourceRule:
    return SourceRule(
        source_id=source_id,
        origin=origin,
        schedule=BusinessDaysSchedule(at=time(16, 0), timezone=ZoneInfo("Europe/Berlin")),
        calendar=CalendarSpec(),
        grace=timedelta(hours=2),
        target=FreshnessTarget(relation=f"raw.{source_id}", loaded_at_field="_loaded_at"),
    )


def entry(
    source_id: str,
    *,
    origin: Origin = Origin.CONFIG,
    location: str = "sources[0]",
) -> SourceEntry:
    return SourceEntry(
        source_id=source_id, origin=origin, rule=rule(source_id, origin=origin), location=location
    )


def test_u_app_01_config_wins_over_manifest_with_w004() -> None:
    config_entry = entry("ecb.fx_rates", location="sources[0]")
    manifest_entry = entry(
        "ecb.fx_rates", origin=Origin.DBT_MANIFEST, location="dbt:source.proj.ecb.fx_rates"
    )
    other = entry(
        "vendor.daily", origin=Origin.DBT_MANIFEST, location="dbt:source.proj.vendor.daily"
    )

    merged, warnings = merge_entries(
        [config_entry], [manifest_entry, other], config_file="freshcal.yml"
    )

    assert [(item.source_id, item.origin) for item in merged] == [
        ("ecb.fx_rates", Origin.CONFIG),
        ("vendor.daily", Origin.DBT_MANIFEST),
    ]
    assert merged[0].rule is config_entry.rule, "the config definition is used entirely"
    assert warnings == [
        Issue(
            "W004",
            "'ecb.fx_rates' is defined in both freshcal.yml and the dbt manifest; using the "
            "config file definition",
            "ecb.fx_rates",
        )
    ]


def test_u_app_02_manifest_internal_duplicates_are_e206_on_each() -> None:
    first = entry(
        "ecb.fx_rates", origin=Origin.DBT_MANIFEST, location="dbt:source.pkg_a.ecb.fx_rates"
    )
    second = entry(
        "ecb.fx_rates", origin=Origin.DBT_MANIFEST, location="dbt:source.pkg_b.ecb.fx_rates"
    )
    unique = entry(
        "vendor.daily", origin=Origin.DBT_MANIFEST, location="dbt:source.pkg_a.vendor.daily"
    )

    merged, warnings = merge_entries([], [first, second, unique])
    by_id = {item.source_id: item for item in merged}

    assert warnings == []
    assert by_id["vendor.daily"].errors == ()
    assert by_id["vendor.daily"].rule is not None
    assert by_id["ecb.fx_rates"].rule is None
    assert [issue.code for issue in by_id["ecb.fx_rates"].errors] == ["E206"]

    # Every member of the duplicated group is marked, each message naming both locations.
    messages = [item.errors[0].message for item in merged if item.errors]
    assert messages == [
        "duplicate source id 'ecb.fx_rates' at dbt:source.pkg_a.ecb.fx_rates and "
        "dbt:source.pkg_b.ecb.fx_rates",
        "duplicate source id 'ecb.fx_rates' at dbt:source.pkg_a.ecb.fx_rates and "
        "dbt:source.pkg_b.ecb.fx_rates",
    ]


def test_u_app_02_config_duplicates_are_marked_before_merging() -> None:
    """The config loader marks its own duplicates; merging keeps them."""
    duplicate = SourceEntry(
        source_id="a.b",
        origin=Origin.CONFIG,
        rule=None,
        errors=(
            Issue("E206", "duplicate source id 'a.b' at sources[0] and sources[1]", "sources[1]"),
        ),
        location="sources[1]",
    )
    merged, warnings = merge_entries([entry("a.b", location="sources[0]"), duplicate], [])
    assert warnings == []
    assert [(item.source_id, [issue.code for issue in item.errors]) for item in merged] == [
        ("a.b", []),
        ("a.b", ["E206"]),
    ]


def test_u_app_03_selection_with_several_patterns() -> None:
    entries = [
        entry("ecb.fx_rates"),
        entry("ecb.rates_archive"),
        entry("vendor.daily"),
        entry("stats.monthly"),
    ]
    assert [item.source_id for item in select_entries(entries, ["ecb.*"])] == [
        "ecb.fx_rates",
        "ecb.rates_archive",
    ]
    assert [item.source_id for item in select_entries(entries, ["*.daily", "stats.*"])] == [
        "vendor.daily",
        "stats.monthly",
    ]
    assert [item.source_id for item in select_entries(entries, None)] == [
        "ecb.fx_rates",
        "ecb.rates_archive",
        "vendor.daily",
        "stats.monthly",
    ]
    assert [item.source_id for item in select_entries(entries, [])] == [
        "ecb.fx_rates",
        "ecb.rates_archive",
        "vendor.daily",
        "stats.monthly",
    ]


def test_u_app_03_selection_is_case_sensitive_and_exact() -> None:
    entries = [entry("ecb.fx_rates"), entry("ecb.fx_rates_daily")]
    assert [item.source_id for item in select_entries(entries, ["ecb.fx_rates"])] == [
        "ecb.fx_rates"
    ]
    with pytest.raises(ConfigError) as excinfo:  # patterns are case-sensitive
        select_entries(entries, ["ECB.*"])
    assert excinfo.value.issue.code == "E210"


def test_u_app_04_no_match_is_e210() -> None:
    with pytest.raises(ConfigError) as excinfo:
        select_entries([entry("a.b")], ["nothing.*"])
    issue = excinfo.value.issue
    assert issue.code == "E210"
    assert issue.message == "--select matched no sources: nothing.*"


def test_u_app_04_no_match_lists_every_pattern() -> None:
    with pytest.raises(ConfigError) as excinfo:
        select_entries([entry("a.b")], ["x.*", "y.*"])
    assert excinfo.value.issue.message == "--select matched no sources: x.*, y.*"


def test_u_app_05_merged_output_is_sorted_by_source_id() -> None:
    config_entries = [entry("z.last"), entry("a.first")]
    manifest_entries = [
        entry("m.middle", origin=Origin.DBT_MANIFEST),
        entry("b.second", origin=Origin.DBT_MANIFEST),
    ]
    merged, _ = merge_entries(config_entries, manifest_entries)
    assert [item.source_id for item in merged] == ["a.first", "b.second", "m.middle", "z.last"]


def test_merge_keeps_erroring_entries() -> None:
    """An invalid source from either side stays in the list, so it can be reported."""
    broken = SourceEntry(
        source_id="broken.source",
        origin=Origin.DBT_MANIFEST,
        rule=None,
        errors=(Issue("E303", "dbt:source.proj.broken.source: no loaded_at_field", "x"),),
        location="dbt:source.proj.broken.source",
    )
    merged, _ = merge_entries([], [broken])
    assert len(merged) == 1
    assert merged[0].rule is None
    assert [issue.code for issue in merged[0].errors] == ["E303"]
