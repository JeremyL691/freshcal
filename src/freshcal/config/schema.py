"""Structural validation of configuration documents (BLUEPRINT.md §4.4).

Stage 1 of the two-stage validation: the committed JSON Schema decides whether a
document has the right shape, and every schema error is mapped to a coded
:class:`Issue` with a precise location such as ``sources[0].schedule.time``.

Two design constraints come from the blueprint: sub-schemas are validated through a
wrapper ``{"$defs": …, "$ref": "#/$defs/<name>"}`` so that ``$ref``s still resolve,
and every error is reported because ``validate`` lists them all; the ``best_match``
error stays first so a caller that shows only one (the ``check`` path) keeps the most
relevant message (CFG-04).

Input hardening (T-7.9): the extended validator below builds *bounded* messages for the
keywords that render an instance (``enum``, ``maxItems``, ``uniqueItems``) because
jsonschema's own versions format ``repr(instance)`` eagerly and a YAML alias bomb
expands to hundreds of megabytes there; every message this module renders goes through
:func:`~freshcal.core.errors.render_value` (CFG-13).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from importlib.resources import files
from typing import Any, Final

from jsonschema import Draft202012Validator, ValidationError
from jsonschema._utils import equal, uniq
from jsonschema.exceptions import best_match
from jsonschema.validators import extend

from freshcal.core.errors import Issue, render_value, truncate

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
        "maxItems",
        "uniqueItems",
        "minProperties",
        "minimum",
        "maximum",
        "not",
    }
)
_REQUIRED_RE: Final[re.Pattern[str]] = re.compile(r"^'([^']+)' is a required property$")


def _load_schema() -> dict[str, Any]:
    resource = files("freshcal.schemas").joinpath("config.v1.schema.json")
    loaded: dict[str, Any] = json.loads(resource.read_text(encoding="utf-8"))
    return loaded


def _bounded_enum(
    validator: Draft202012Validator, enums: Any, instance: Any, schema: Any
) -> Iterator[ValidationError]:
    """jsonschema's ``enum`` with a bounded message (CFG-13)."""
    if all(not equal(each, instance) for each in enums):
        yield ValidationError(f"{render_value(instance)} is not one of {render_value(enums)}")


def _bounded_max_items(
    validator: Draft202012Validator, maximum: Any, instance: Any, schema: Any
) -> Iterator[ValidationError]:
    """jsonschema's ``maxItems`` with a bounded message (CFG-13)."""
    if validator.is_type(instance, "array") and len(instance) > maximum:
        message = "is expected to be empty" if maximum == 0 else "is too long"
        yield ValidationError(f"{render_value(instance)} {message}")


def _bounded_unique_items(
    validator: Draft202012Validator, unique: Any, instance: Any, schema: Any
) -> Iterator[ValidationError]:
    """jsonschema's ``uniqueItems`` with a bounded message (CFG-13)."""
    if unique and validator.is_type(instance, "array") and not uniq(instance):
        yield ValidationError(f"{render_value(instance)} has non-unique elements")


#: ``Draft202012Validator`` whose instance-rendering messages stay small.
#:
#: Only the message text changes: the yielded errors carry the same ``validator``,
#: ``validator_value``, ``instance`` and path, so ``_map_error`` maps them exactly as
#: before. jsonschema's own versions format ``repr(instance)`` eagerly, which turns a
#: 380-byte YAML alias bomb into gigabytes of strings before this module can truncate
#: anything (CFG-13). ``extend`` is jsonschema's supported way to replace keywords;
#: subclassing the validator class directly is deprecated.
_BoundedValidator = extend(  # type: ignore[no-untyped-call]  # the stubs leave extend untyped
    Draft202012Validator,
    {
        "enum": _bounded_enum,
        "maxItems": _bounded_max_items,
        "uniqueItems": _bounded_unique_items,
    },
)

_SCHEMA: Final[dict[str, Any]] = _load_schema()
_DOCUMENT_VALIDATOR: Final[Draft202012Validator] = _BoundedValidator(_SCHEMA)
_SUBSCHEMA_VALIDATORS: Final[dict[str, Draft202012Validator]] = {
    name: _BoundedValidator({"$defs": _SCHEMA["$defs"], "$ref": f"#/$defs/{name}"})
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
        return f"at least {error.validator_value} characters"
    pattern = error.schema.get("pattern") if isinstance(error.schema, Mapping) else None
    if pattern is not None:
        return f"does not match {pattern}"
    return str(error.validator)


def _unexpected_keys(error: ValidationError) -> list[object]:
    """The keys the schema rejected, read from the instance.

    Reading them structurally instead of parsing jsonschema's message names every key,
    including YAML keys that are not strings (CFG-21). The committed schema has no
    ``patternProperties``, so "unknown" means "not listed under ``properties``".
    """
    instance = error.instance
    if not isinstance(instance, Mapping):
        return []
    properties = error.schema.get("properties") if isinstance(error.schema, Mapping) else None
    known = set(properties) if isinstance(properties, Mapping) else set()
    return [key for key in instance if key not in known]


def _key_text(key: object) -> str:
    """A bounded, unambiguous rendering of a mapping key for the E101 message."""
    if isinstance(key, str):
        return f"'{truncate(key)}'"
    return render_value(key)


def _value_text(value: object) -> str:
    """The E104 form of a value: quoted for strings, a bounded repr otherwise."""
    if isinstance(value, str):
        return f"'{truncate(value)}'"
    return render_value(value)


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
                issues.append(_issue("E101", location, f"unknown field {_key_text(key)}"))
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
        return [
            _issue("E104", location, f"{_value_text(error.instance)} is not one of: {rendered}")
        ]

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
        # ``uniqueItems`` can fire before ``maxItems`` (found on 2026-09-29). CFG-11:
        # ``maxItems`` is mapped too, so seven *distinct* days reach E406 as well.
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
        # CFG-12: the schema's only ``minLength`` guard on ``cron`` is a rough proxy for
        # "at least five fields"; count the fields so the message matches E202's
        # ``expected 5 fields, got N`` reason (a cron with five 1-character fields is
        # nine characters, so a shorter expression always has too few fields).
        if validator == "minLength" and field == "cron":
            expression = error.instance if isinstance(error.instance, str) else ""
            return [
                _issue(
                    "E202",
                    location,
                    f"invalid cron expression {render_value(expression)}: "
                    f"expected 5 fields, got {len(expression.split())}",
                )
            ]
        return [
            _issue(
                "E106",
                location,
                f"invalid value {render_value(error.instance)}: {_hint(field, error)}",
            )
        ]

    # Fallback: keep the schema's own wording rather than dropping the error.
    return [
        _issue(
            "E106",
            location,
            f"invalid value {render_value(error.instance)}: {truncate(error.message)}",
        )
    ]


def _issues(
    instance: object,
    validator: Draft202012Validator,
    prefix: str,
    *,
    dbt_rule: bool = False,
) -> list[Issue]:
    """Every schema error, mapped and de-duplicated by path, with ``best_match`` first.

    Several schema errors can describe one mistake at the same path (a seven-entry
    ``weekend`` fails both ``uniqueItems`` and ``maxItems``); the path keeps the best
    match and the others are dropped. One error can still yield several issues (one per
    unexpected key), and the best match is placed first so a caller that shows a single
    message keeps the most relevant one.
    """
    errors = list(validator.iter_errors(instance))
    if not errors:
        return []
    best = best_match(errors)
    by_path: dict[tuple[object, ...], ValidationError] = {}
    for error in [best, *(error for error in errors if error is not best)]:
        by_path.setdefault(tuple(error.absolute_path), error)
    return [
        issue
        for error in by_path.values()
        for issue in _map_error(error, prefix, dbt_rule=dbt_rule)
    ]


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
