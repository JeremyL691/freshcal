"""Loading and parsing helpers for the golden scenarios (§9.4).

Each scenario's rule is a ``meta.freshcal``-style mapping parsed by the real config
code (``validate_dbt_rule`` + ``build_rule``) with ``relation: golden`` and
``loaded_at_field: _loaded_at`` injected, so the scenarios exercise the same path the
application does.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from freshcal.config.loader import Defaults, build_rule, parse_named_calendars
from freshcal.config.schema import validate_dbt_rule
from freshcal.config.yaml_loader import load_yaml_file
from freshcal.core.model import CalendarSpec, Origin, RawObservation, SourceRule

SCENARIOS_PATH = Path(__file__).with_name("scenarios.yaml")
PROCESS_TIME_ZONES = ("UTC", "Pacific/Kiritimati", "America/Adak")


def load_scenarios() -> list[dict[str, Any]]:
    loaded = load_yaml_file(SCENARIOS_PATH)
    assert isinstance(loaded, list)
    scenarios = []
    for entry in loaded:
        assert isinstance(entry, dict), entry
        scenarios.append(entry)
    return scenarios


SCENARIOS = load_scenarios()
SCENARIO_IDS = [str(entry["id"]) for entry in SCENARIOS]


def named_calendars_of(entry: Mapping[str, Any]) -> dict[str, CalendarSpec]:
    calendars = entry.get("calendars")
    if not calendars:
        return {}
    return parse_named_calendars({"calendars": calendars}, "", SCENARIOS_PATH.parent)


def rule_of(entry: Mapping[str, Any]) -> SourceRule:
    """Parse the scenario's rule with the real config code (§9.4)."""
    mapping = entry["rule"]
    assert isinstance(mapping, dict)
    location = f"{entry['id']}.rule"
    issues = validate_dbt_rule(mapping, location)
    assert issues == [], f"{entry['id']}: {issues}"
    return build_rule(
        mapping,
        source_id=str(entry["id"]),
        origin=Origin.CONFIG,
        relation="golden",
        loaded_at_field="_loaded_at",
        filter=None,
        defaults=Defaults(),
        named_calendars=named_calendars_of(entry),
        location=location,
        config_dir=SCENARIOS_PATH.parent,
    )


def observation_of(entry: Mapping[str, Any]) -> RawObservation:
    value = entry.get("observed")
    if value is None:
        return RawObservation(None)
    assert isinstance(value, str)
    return RawObservation(datetime.fromisoformat(value))


def instant(value: object) -> datetime | None:
    if value is None:
        return None
    assert isinstance(value, str)
    return datetime.fromisoformat(value)
