"""dbt manifest catalog (BLUEPRINT.md §4.8, §7.7).

FreshCal never parses ``sources.yml`` and never renders Jinja: it reads the manifest dbt
already wrote, which is the resolved, canonical form of the project. Three claims are
kept apart here and in the documentation:

- **format recognized:** manifest schema v12 (``metadata.dbt_schema_version``);
- **tested:** dbt-core 1.12.5 with dbt-duckdb 1.11.0 (the committed fixture);
- **declared by others, untested:** dbt 1.8-1.11 and "v2.0" also declare v12; they are
  accepted, and a structural difference surfaces as a per-source error rather than a
  crash.

Rule conversion is shared with the standalone config loader: the same
``validate_dbt_rule`` + ``build_rule`` path turns a ``meta.freshcal`` block into a
:class:`SourceRule`, so the two sources of rules cannot drift.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from freshcal.config.loader import Defaults, build_rule, calendar_warnings
from freshcal.config.schema import validate_dbt_rule
from freshcal.core.errors import ConfigError, Issue
from freshcal.core.model import CalendarSpec, Origin, SourceEntry
from freshcal.core.ports import SourceCatalog

__all__ = ["SUPPORTED_MANIFEST_VERSION", "DbtManifestCatalog"]

SUPPORTED_MANIFEST_VERSION = 12
_VERSION_PATTERN = re.compile(r"/manifest/v(\d+)\.json$")
_META_PATH = "meta.freshcal"


class DbtManifestCatalog(SourceCatalog):
    """Turns the sources of a dbt manifest into :class:`SourceEntry` objects."""

    def __init__(
        self,
        path: Path,
        defaults: Defaults,
        named_calendars: Mapping[str, CalendarSpec],
    ) -> None:
        self._path = path
        self._defaults = defaults
        self._named_calendars = named_calendars

    def entries(self) -> list[SourceEntry]:
        """Every manifest source with a ``freshcal`` rule, in manifest order.

        Nodes without a rule in either ``meta.freshcal`` or ``config.meta.freshcal`` are
        not FreshCal-managed and are skipped. An unreadable file or an unsupported schema
        version raises ``ConfigError`` (fatal, exit 2); anything attributable to one node
        becomes that entry's error.
        """
        document = self._read()
        sources = cast("dict[str, object]", document["sources"])
        entries: list[SourceEntry] = []
        for unique_id, raw_node in sources.items():
            if not isinstance(raw_node, Mapping):
                continue
            rule_mapping = _rule_mapping(raw_node)
            if rule_mapping is None:
                continue
            entries.append(self._entry(unique_id, raw_node, rule_mapping))
        return entries

    def _read(self) -> dict[str, object]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigError(
                Issue("E302", f"{self._path}: cannot read dbt manifest: {error}")
            ) from error
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as error:
            raise ConfigError(
                Issue("E302", f"{self._path}: cannot read dbt manifest: {error}")
            ) from error
        if not isinstance(loaded, dict) or not isinstance(loaded.get("sources"), dict):
            raise ConfigError(
                Issue(
                    "E302",
                    f"{self._path}: cannot read dbt manifest: expected an object with a "
                    "'sources' object",
                )
            )

        metadata = loaded.get("metadata")
        declared = metadata.get("dbt_schema_version") if isinstance(metadata, dict) else None
        found = declared if isinstance(declared, str) else "missing"
        match = _VERSION_PATTERN.search(found)
        if match is None or int(match.group(1)) != SUPPORTED_MANIFEST_VERSION:
            raise ConfigError(
                Issue(
                    "E301",
                    f"{self._path}: unsupported dbt manifest schema '{found}'; FreshCal 0.1 "
                    "reads manifest schema v12 only (tested with dbt-core 1.12.5)",
                )
            )
        return loaded

    def _entry(
        self, unique_id: str, node: Mapping[str, object], rule_mapping: Mapping[str, object]
    ) -> SourceEntry:
        node_location = f"dbt:{unique_id}"
        rule_location = f"{node_location}:{_META_PATH}"
        source_id = f"{node.get('source_name')}.{node.get('name')}"

        issues = validate_dbt_rule(rule_mapping, rule_location)
        if issues:
            return SourceEntry(
                source_id=source_id,
                origin=Origin.DBT_MANIFEST,
                rule=None,
                errors=tuple(issues),
                location=node_location,
            )

        loaded_at_field = rule_mapping.get("loaded_at_field") or node.get("loaded_at_field")
        if not loaded_at_field:
            return SourceEntry(
                source_id=source_id,
                origin=Origin.DBT_MANIFEST,
                rule=None,
                errors=(
                    Issue(
                        "E303",
                        f"{node_location}: no loaded_at_field; set loaded_at_field on the dbt "
                        "source or in meta.freshcal (loaded_at_query is not supported in v0.1)",
                        node_location,
                    ),
                ),
                location=node_location,
            )

        filter_text = rule_mapping.get("filter") or _freshness_filter(node)
        try:
            rule = build_rule(
                rule_mapping,
                source_id=source_id,
                origin=Origin.DBT_MANIFEST,
                relation=str(node.get("relation_name") or ""),
                loaded_at_field=str(loaded_at_field),
                filter=str(filter_text) if filter_text is not None else None,
                defaults=self._defaults,
                named_calendars=self._named_calendars,
                location=node_location,
                config_dir=self._path.parent,
            )
        except ConfigError as error:
            return SourceEntry(
                source_id=source_id,
                origin=Origin.DBT_MANIFEST,
                rule=None,
                errors=(error.issue,),
                location=node_location,
            )
        return SourceEntry(
            source_id=source_id,
            origin=Origin.DBT_MANIFEST,
            rule=rule,
            warnings=calendar_warnings(rule.calendar, source_id=source_id, location=source_id),
            location=node_location,
        )


def _rule_mapping(node: Mapping[str, object]) -> Mapping[str, object] | None:
    """The node's ``meta.freshcal``, else ``config.meta.freshcal``, else ``None``."""
    for candidate in (node.get("meta"), _nested(node, "config", "meta")):
        if isinstance(candidate, Mapping):
            rule = candidate.get("freshcal")
            if isinstance(rule, Mapping):
                return rule
    return None


def _freshness_filter(node: Mapping[str, object]) -> object | None:
    freshness = node.get("freshness")
    if isinstance(freshness, Mapping):
        return freshness.get("filter")
    return None


def _nested(node: Mapping[str, object], *keys: str) -> object | None:
    current: object = node
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current
