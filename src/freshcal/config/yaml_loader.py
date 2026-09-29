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
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from freshcal.core.errors import ConfigError, Issue

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


class FreshCalLoader(yaml.SafeLoader):
    """``SafeLoader`` with the timestamp and base-60 resolvers replaced."""


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

    Raises :class:`ConfigError` with code ``E100`` for any syntax, resolver, or
    constructor error.
    """
    try:
        return yaml.load(text, Loader=FreshCalLoader)
    except yaml.YAMLError as error:
        raise ConfigError(_syntax_issue(error, source)) from error


def load_yaml_file(path: Path) -> object:
    """Read and parse ``path``; a missing file raises ``ConfigError`` ``E110``."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigError(Issue("E110", f"config file not found: {path}", str(path))) from error
    return load_yaml(text, source=str(path))
