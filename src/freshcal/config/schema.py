"""Structural validation of configuration documents (BLUEPRINT.md §4.4).

Stage 1 of the two-stage validation: the committed JSON Schema decides whether a
document has the right shape, and every schema error is mapped to a coded
:class:`Issue` with a precise location such as ``sources[0].schedule.time``.

Two design constraints come from the blueprint: sub-schemas are validated through a
wrapper ``{"$defs": …, "$ref": "#/$defs/<name>"}`` so that ``$ref``s still resolve,
and each call reports the single most relevant error (``best_match``) because the
schema is written so that one mistake yields one error.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from importlib.resources import files
from typing import Any, Final

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.exceptions import best_match

from freshcal.core.errors import Issue

__all__ = [
    "validate_dbt_rule",
    "validate_document",
    "validate_override_file",
    "validate_source",
]

HINT_TIME: Final[str] = 'expected "HH:MM" (24-hour), e.g. "16:00"'
HINT_DATE: Final[str] = 'expected "YYYY-MM-DD"'
HINT_DURATION: Final[str] = 'expected a duration such as "90m", "2h", "1d6h"'
HINT_DSN_ENV: Final[str] = "expected an environment variable name such as FRESHCAL_PG_DSN"
HINT_SOURCE_NAME: Final[str] = (
    'expected "source" or "source.table" using letters, digits, underscores'
)

_SECRET_FIELDS: Final[frozenset[str]] = frozenset(
    {"dsn", "password", "url", "uri", "conninfo", "user"}
)
_DATE_FIELDS: Final[frozenset[str]] = frozenset(
    {"active_from", "valid_until", "working_days", "non_working_days"}
)
_RANGE_VALIDATORS: Final[frozenset[str]] = frozenset(
    {
        "pattern",
        "minLength",
        "minItems",
        "uniqueItems",
        "minProperties",
        "minimum",
        "maximum",
        "not",
    }
)
_REQUIRED_RE: Final[re.Pattern[str]] = re.compile(r"^'([^']+)' is a required property$")
_QUOTED_RE: Final[re.Pattern[str]] = re.compile(r"'([^']+)'")


def _load_schema() -> dict[str, Any]:
    resource = files("freshcal.schemas").joinpath("config.v1.schema.json")
    loaded: dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    return loaded


_SCHEMA: Final[dict[str, Any]] = _load_schema()
_DOCUMENT_VALIDATOR: Final[Draft202012Validator] = Draft202012Validator(_SCHEMA)
_SUBSCHEMA_VALIDATORS: Final[dict[str, Draft202012Validator]] = {
    name: Draft202012Validator({"$defs": _SCHEMA["$defs"], "$ref": f"#/$defs/{name}"})
    for name in ("source", "dbtRule", "overrideDays")
}


def _last_field(path: Sequence[object]) -> str:
    """The last string element of a path; list indices are skipped."""
    for element in reversed(path):
        if isinstance(element, str):
            return element
    return ""


def _render_location(prefix: str, path: Sequence[object]) -> str:
    """Render ``absolute_path`` as ``prefix.a[0].b``."""
    rendered = prefix
    for element in path:
        if isinstance(element, int) and not isinstance(element, bool):
            rendered += f"[{element}]"
        else:
            rendered += f".{element}" if rendered else str(element)
    return rendered


def _describe_value(value: object) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _issue(code: str, location: str, text: str) -> Issue:
    """Build an issue whose message carries the location prefix when there is one."""
    return Issue(code, f"{location}: {text}" if location else text, location)


def _hint(field: str, error: ValidationError) -> str:
    if field == "time":
        return HINT_TIME
    if field in _DATE_FIELDS:
        return HINT_DATE
    if field in {"grace", "duration"}:
        return HINT_DURATION
    if field == "dsn_env":
        return HINT_DSN_ENV
    if field in {"name", "sourceId", "identifier"}:
        return HINT_SOURCE_NAME
    validator = error.validator
    if validator == "uniqueItems":
        return "items must be unique"
    if validator == "minItems":
        return f"expected at least {error.validator_value} item(s)"
    if validator == "minProperties":
        return "expected at least one field"
    if validator == "minLength":
        return "must not be empty"
    pattern = error.schema.get("pattern") if isinstance(error.schema, Mapping) else None
    if pattern is not None:
        return f"does not match {pattern}"
    return str(error.validator)


def _unexpected_keys(error: ValidationError) -> list[str]:
    message = error.message
    start = message.find("(")
    end = message.rfind(")")
    if start == -1 or end == -1:
        return []
    return _QUOTED_RE.findall(message[start:end])


def _map_error(error: ValidationError, prefix: str, *, dbt_rule: bool) -> list[Issue]:
    """Map one schema error to one issue (or one issue per unexpected key)."""
    path = list(error.absolute_path)
    location = _render_location(prefix, path)
    field = _last_field(path)
    validator = error.validator

    if validator == "additionalProperties":
        keys = _unexpected_keys(error) or [field]
        issues: list[Issue] = []
        for key in keys:
            if field == "connection" and key in _SECRET_FIELDS:
                issues.append(
                    _issue(
                        "E105",
                        location,
                        f"'{key}' is not allowed; secrets must not be stored in config files. "
                        "Put the DSN in an environment variable and set dsn_env to its name",
                    )
                )
            elif key == "on_non_business_day" and field == "schedule":
                issues.append(
                    _issue("E205", location, "on_non_business_day is only valid for kind: cron")
                )
            elif dbt_rule and key in {"name", "relation"}:
                issues.append(
                    _issue(
                        "E304",
                        location,
                        f"'{key}' cannot be set in meta.freshcal because dbt provides it",
                    )
                )
            else:
                issues.append(_issue("E101", location, f"unknown field '{key}'"))
        return issues

    if validator == "required":
        match = _REQUIRED_RE.match(error.message)
        missing = match.group(1) if match else str(error.validator_value)
        return [_issue("E102", location, f"missing required field '{missing}'")]

    if validator == "type":
        if (
            field == "time"
            and isinstance(error.instance, int)
            and not isinstance(error.instance, bool)
        ):
            return [
                _issue(
                    "E103",
                    location,
                    'expected a quoted time such as "16:00", got the integer '
                    f"{error.instance}; YAML 1.1 reads unquoted 16:00 as the base-60 number "
                    '960, so write time: "16:00"',
                )
            ]
        return [
            _issue(
                "E103",
                location,
                f"expected {error.validator_value}, got {_describe_value(error.instance)}",
            )
        ]

    if validator in {"enum", "const"}:
        if field == "version":
            return [_issue("E104", location, "unsupported config version; expected 1")]
        allowed = error.validator_value
        if not isinstance(allowed, list | tuple):
            allowed = [allowed]
        rendered = ", ".join(str(value) for value in allowed)
        return [_issue("E104", location, f"'{error.instance}' is not one of: {rendered}")]

    if validator in _RANGE_VALIDATORS:
        if field == "business_day":
            return [
                _issue(
                    "E204",
                    location,
                    "business_day must be an integer between -23 and 23, excluding 0; "
                    f"got {error.instance}",
                )
            ]
        # A weekend list with more than six entries is E406 however the schema reports
        # it: with only seven weekday names, seven entries always repeat one, so
        # ``uniqueItems`` can fire before ``maxItems`` (found on 2026-09-29).
        weekend_too_long = field == "weekend" and (
            validator == "maxItems"
            or (
                validator == "uniqueItems"
                and isinstance(error.instance, list)
                and len(error.instance) > 6
            )
        )
        if weekend_too_long:
            return [_issue("E406", location, "weekend may contain at most 6 days")]
        return [
            _issue("E106", location, f"invalid value {error.instance!r}: {_hint(field, error)}")
        ]

    # Fallback: keep the schema's own wording rather than dropping the error.
    return [_issue("E106", location, f"invalid value {error.instance!r}: {error.message}")]


def _issues(
    instance: object,
    validator: Draft202012Validator,
    prefix: str,
    *,
    dbt_rule: bool = False,
) -> list[Issue]:
    error = best_match(validator.iter_errors(instance))
    if error is None:
        return []
    return _map_error(error, prefix, dbt_rule=dbt_rule)


def validate_document(doc: object) -> list[Issue]:
    """Validate the top-level document, ignoring the ``sources`` array.

    Source entries are validated one by one with :func:`validate_source` so that an
    error in one source can mark that source without aborting the whole run. A
    ``sources`` value that is not a list is kept, so its type error is still reported.
    """
    if isinstance(doc, Mapping) and isinstance(doc.get("sources"), list):
        doc = {**doc, "sources": []}
    return _issues(doc, _DOCUMENT_VALIDATOR, "")


def validate_source(doc: object, index: int) -> list[Issue]:
    """Validate one ``sources[i]`` entry; locations are ``sources[i]…``."""
    return _issues(doc, _SUBSCHEMA_VALIDATORS["source"], f"sources[{index}]")


def validate_dbt_rule(doc: object, location: str) -> list[Issue]:
    """Validate one ``meta.freshcal`` rule; ``location`` names the node and field."""
    return _issues(doc, _SUBSCHEMA_VALIDATORS["dbtRule"], location, dbt_rule=True)


def validate_override_file(doc: object, location: str) -> list[Issue]:
    """Validate an override file against ``$defs/overrideDays``."""
    return _issues(doc, _SUBSCHEMA_VALIDATORS["overrideDays"], location)
