"""YAML loading without the two YAML 1.1 traps (BLUEPRINT.md §4.1).

``yaml.SafeLoader`` already refuses arbitrary Python objects, but it still applies
two implicit resolvers that surprise FreshCal users:

1. ``timestamp``: unquoted ``2026-01-04`` becomes a ``date``;
2. base-60 ``int``/``float``: unquoted ``16:00`` becomes the integer 960.

Both are removed here (the ``int``/``float`` patterns are PyYAML's own, minus their
base-60 alternatives), so dates and times stay strings and are validated later with
our own messages. This affects only FreshCal's own files: dbt has already parsed
``meta.freshcal`` with standard YAML 1.1 rules before FreshCal sees the manifest
(§4.1), which is why the manifest adapter reports E103 with a "quote it" hint instead
of guessing.

Two hardenings come from the v0.1.0 audit (CFG-09, CFG-16): a duplicate mapping key is
rejected with the second key's line and column instead of silently keeping the last
value, and every read failure of the config file is an ``E110`` with a reason.
"""

from __future__ import annotations

import re
from collections.abc import Hashable
from pathlib import Path
from typing import Any

import yaml

from freshcal.core.errors import ConfigError, Issue, read_failure_reason

__all__ = ["load_yaml", "load_yaml_file"]

_INT_PATTERN = re.compile(
    r"""^(?:[-+]?0b[0-1_]+
        |[-+]?0[0-7_]+
        |[-+]?(?:0|[1-9][0-9_]*)
        |[-+]?0x[0-9a-fA-F_]+)$""",
    re.X,
)
# PyYAML 6.0.3's float pattern with the base-60 alternative
# ``[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*`` removed. Note that the exponent
# requires an explicit sign in PyYAML (``1.0e+3`` is a float, ``1.0e3`` is a string);
# this pattern is kept identical so FreshCal's own files parse like any other YAML.
_FLOAT_PATTERN = re.compile(
    r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?
        |\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?
        |[-+]?\.(?:inf|Inf|INF)
        |\.(?:nan|NaN|NAN))$""",
    re.X,
)
_REPLACED_TAGS = frozenset(
    {
        "tag:yaml.org,2002:timestamp",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:float",
    }
)
_MERGE_TAG = "tag:yaml.org,2002:merge"


class FreshCalLoader(yaml.SafeLoader):
    """``SafeLoader`` with the timestamp and base-60 resolvers replaced."""

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[object, object]:
        """Build a mapping, rejecting a literal duplicate key (CFG-16).

        PyYAML's ``SafeConstructor`` keeps the last value silently. The duplicate is a
        configuration mistake (``grace: 1h`` followed by ``grace: 300d``), so it is a
        YAML error whose message carries the second key's line and column. The check
        runs before ``flatten_mapping``: a key overridden through a YAML merge key
        (``<<``) is the language's documented default mechanism, not a duplicate.
        """
        if isinstance(node, yaml.MappingNode):
            self._reject_duplicate_keys(node)
        return super().construct_mapping(node, deep)

    def _reject_duplicate_keys(self, node: yaml.MappingNode) -> None:
        seen: set[object] = set()
        for key_node, _ in node.value:
            if key_node.tag == _MERGE_TAG:
                continue  # ``flatten_mapping`` consumes merge keys before construction
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, Hashable):
                continue  # the base constructor reports the unhashable key itself
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)


def _install_resolvers(cls: type[yaml.SafeLoader]) -> None:
    """Copy SafeLoader's resolvers, drop the three tags, re-add the plain patterns."""
    resolvers: dict[object, list[tuple[str, re.Pattern[str]]]] = {}
    for first_char, entries in yaml.SafeLoader.yaml_implicit_resolvers.items():
        resolvers[first_char] = [
            (tag, pattern) for tag, pattern in entries if tag not in _REPLACED_TAGS
        ]
    cls.yaml_implicit_resolvers = resolvers
    cls.add_implicit_resolver("tag:yaml.org,2002:int", _INT_PATTERN, list("-+0123456789"))
    cls.add_implicit_resolver("tag:yaml.org,2002:float", _FLOAT_PATTERN, list("-+0123456789."))


_install_resolvers(FreshCalLoader)


def _syntax_issue(error: yaml.YAMLError, source: str) -> Issue:
    """Turn a PyYAML error into ``E100`` with a ``file: YAML syntax error`` message."""
    mark = getattr(error, "problem_mark", None)
    problem = getattr(error, "problem", None) or str(error)
    if mark is None:
        detail = problem
    else:
        detail = f"{problem} at line {mark.line + 1}, column {mark.column + 1}"
    return Issue("E100", f"{source}: YAML syntax error: {detail}", source)


def load_yaml(text: str, *, source: str) -> object:
    """Parse ``text`` as YAML; ``source`` names the origin in error messages.

    Raises :class:`ConfigError` with code ``E100`` for any syntax, resolver,
    constructor, or over-deep error.
    """
    try:
        return yaml.load(text, Loader=FreshCalLoader)
    except yaml.YAMLError as error:
        raise ConfigError(_syntax_issue(error, source)) from error
    except RecursionError as error:
        raise ConfigError(
            Issue("E100", f"{source}: YAML syntax error: document is too deeply nested", source)
        ) from error


def load_yaml_file(path: Path) -> object:
    """Read and parse ``path``; any read failure is an ``E110`` with a reason (CFG-09)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        reason = read_failure_reason(error)
        raise ConfigError(
            Issue("E110", f"cannot read config file {path}: {reason}", str(path))
        ) from error
    return load_yaml(text, source=str(path))
