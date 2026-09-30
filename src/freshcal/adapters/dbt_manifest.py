"""dbt manifest catalog.

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
from freshcal.core.errors import ConfigError, Issue, read_failure_reason, truncate
from freshcal.core.model import CalendarSpec, Origin, SourceEntry
from freshcal.core.ports import SourceCatalog

__all__ = ["SUPPORTED_MANIFEST_VERSION", "DbtManifestCatalog"]

SUPPORTED_MANIFEST_VERSION = 12
_VERSION_PATTERN = re.compile(r"/manifest/v(\d+)\.json$")


class DbtManifestCatalog(SourceCatalog):
    """Turns the sources of a dbt manifest into :class:`SourceEntry` objects.

    ``config_dir`` is the directory of the FreshCal config file that declares
    ``dbt.manifest``; relative override paths inside ``meta.freshcal`` resolve against
    it, exactly like every other relative path in a config file.
    """

    def __init__(
        self,
        path: Path,
        defaults: Defaults,
        named_calendars: Mapping[str, CalendarSpec],
        *,
        config_dir: Path,
    ) -> None:
        self._path = path
        self._defaults = defaults
        self._named_calendars = named_calendars
        self._config_dir = config_dir

    def entries(self) -> list[SourceEntry]:
        """Every manifest source with a ``freshcal`` rule, in manifest order.

        Nodes without a ``freshcal`` key in either ``meta`` or ``config.meta`` are not
        FreshCal-managed and are skipped. A ``freshcal`` key whose value is not a mapping
        is a per-source error (``E103``), not a skip. An unreadable file or an
        unsupported schema version raises ``ConfigError`` (fatal, exit 2); anything
        attributable to one node becomes that entry's error.
        """
        document = self._read()
        try:
            return self._entries_from(document)
        except (RecursionError, ValueError) as error:
            # A manifest whose *contents* overflow the parser (deep nesting, aliased
            # structures) is unreadable, not an internal error.
            raise ConfigError(
                Issue("E302", f"{self._path}: cannot read dbt manifest: {_decode_reason(error)}")
            ) from error

    def _entries_from(self, document: dict[str, object]) -> list[SourceEntry]:
        sources = cast("dict[str, object]", document["sources"])
        entries: list[SourceEntry] = []
        for unique_id, raw_node in sources.items():
            if not isinstance(raw_node, Mapping):
                continue
            found = _rule_value(raw_node)
            if found is None:
                continue
            rule_value, rule_path = found
            entries.append(self._entry(unique_id, raw_node, rule_value, rule_path))
        return entries

    def _read(self) -> dict[str, object]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise ConfigError(
                Issue(
                    "E302",
                    f"{self._path}: cannot read dbt manifest: {read_failure_reason(error)}",
                )
            ) from error
        try:
            loaded = json.loads(text)
        except (RecursionError, ValueError) as error:
            raise ConfigError(
                Issue("E302", f"{self._path}: cannot read dbt manifest: {_decode_reason(error)}")
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
        self,
        unique_id: str,
        node: Mapping[str, object],
        rule_value: object,
        rule_path: str,
    ) -> SourceEntry:
        node_location = f"dbt:{unique_id}"
        rule_location = f"{node_location}:{rule_path}"
        source_id = _source_id(node, unique_id)
        errors: list[Issue] = list(_identity_issues(unique_id, node))

        # an empty or missing relation is a config error, not a usable rule.
        relation = node.get("relation_name")
        if not isinstance(relation, str) or not relation:
            errors.append(
                Issue(
                    "E102",
                    f"{node_location}: missing required field 'relation_name'",
                    node_location,
                )
            )

        # a `freshcal` key present but not a mapping is a per-source E103
        # (routed through the schema so the wording matches every other E103).
        errors.extend(validate_dbt_rule(rule_value, rule_location))
        rule_mapping = rule_value if isinstance(rule_value, Mapping) else None
        if errors or rule_mapping is None:
            return SourceEntry(
                source_id=source_id,
                origin=Origin.DBT_MANIFEST,
                rule=None,
                errors=tuple(errors),
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
        assert isinstance(relation, str)  # a non-string relation appended E102 above and returned
        try:
            rule = build_rule(
                rule_mapping,
                source_id=source_id,
                origin=Origin.DBT_MANIFEST,
                relation=relation,
                loaded_at_field=str(loaded_at_field),
                filter=str(filter_text) if filter_text is not None else None,
                defaults=self._defaults,
                named_calendars=self._named_calendars,
                location=node_location,
                config_dir=self._config_dir,
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
            warnings=calendar_warnings(rule.calendar, source_id=source_id),
            location=node_location,
        )


def _decode_reason(error: BaseException) -> str:
    """A bounded detail for an ``E302`` decode failure."""
    if isinstance(error, RecursionError):
        return "document is too deeply nested"
    return truncate(str(error))


def _rule_value(node: Mapping[str, object]) -> tuple[object, str] | None:
    """The raw ``meta.freshcal`` (else ``config.meta.freshcal``) value and its path.

    A key that is present is returned even when its value is not a mapping, so the
    caller can report it instead of silently skipping the node.
    """
    for path, candidate in (
        ("meta", node.get("meta")),
        ("config.meta", _nested(node, "config", "meta")),
    ):
        if isinstance(candidate, Mapping) and "freshcal" in candidate:
            return candidate.get("freshcal"), f"{path}.freshcal"
    return None


def _source_id(node: Mapping[str, object], unique_id: str) -> str:
    """``source_name.name``, or the manifest's own ``unique_id`` when unusable."""
    source_name = node.get("source_name")
    name = node.get("name")
    if isinstance(source_name, str) and source_name and isinstance(name, str) and name:
        return f"{source_name}.{name}"
    return unique_id


def _identity_issues(unique_id: str, node: Mapping[str, object]) -> tuple[Issue, ...]:
    """``E102`` for every manifest field the source ID needs but does not have."""
    issues: list[Issue] = []
    for field in ("source_name", "name"):
        value = node.get(field)
        if not isinstance(value, str) or not value:
            location = f"dbt:{unique_id}"
            issues.append(Issue("E102", f"{location}: missing required field '{field}'", location))
    return tuple(issues)


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
